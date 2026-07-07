"""
OASIS模拟运行器
在后台运行模拟并记录每个Agent的动作，支持实时状态监控
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# 标记是否已注册清理函数
_cleanup_registered = False

# 平台检测
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """运行器状态"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """Agent动作记录"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """每轮摘要"""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """模拟运行状态（实时）"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # 进度信息
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # 各平台独立轮次和模拟时间（用于双平台并行显示）
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # 平台状态
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # 平台完成状态（通过检测 actions.jsonl 中的 simulation_end 事件）
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # 每轮摘要
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # 最近动作（用于前端实时展示）
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # 时间戳
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # 错误信息
    error: Optional[str] = None
    
    # 进程ID/进程组ID（用于停止 + 重启后回收孤儿，EXECPLAN2 F-12-0/F-6-5/F-6-11）
    process_pid: Optional[int] = None
    process_pgid: Optional[int] = None

    # 平台「是否启用」的权威来源（启动时按 platform 一次性写定并持久化）。
    # 旧逻辑用 actions.jsonl 是否存在来推断启用，启动早期/失败时会误判 → 过早 COMPLETED（F-6-10）。
    twitter_enabled: bool = False
    reddit_enabled: bool = False

    # 图谱记忆更新的「请求 vs 实际」状态（F-6-12）：避免请求开启但创建失败却对外报告已开启。
    graph_memory_requested: bool = False
    graph_memory_active: bool = False
    graph_memory_error: Optional[str] = None

    # 轮数截断记录（T3.7）：当显式 max_rounds 小于按时长算出的完整轮数时，
    # 把「本应跑多少轮 / 实际跑多少轮」记为一等字段，让 UI/报告能看见这次预测被裁短了。
    rounds_truncated_from: Optional[int] = None
    rounds_truncated_to: Optional[int] = None

    # RUN-7: 断点续跑的诚实记账——本次运行从第几轮之后续起（None=全新运行）。
    # 报告/健康门可据此区分「一气呵成」与「崩溃后续跑」的样本。
    resumed_from_round: Optional[int] = None

    # EXECPLAN2 F-6-1：守护本对象可变标量计数器的可重入锁。
    # get_run_state() 返回缓存的同一个对象；监控线程持续 mutate（add_action / current_round /
    # twitter_*/reddit_* / counts），而 Flask 请求线程并发调用 to_dict()/to_detail_dict() 序列化。
    # 用可重入锁保证「所有标量计数器的快照内部自洽」，避免轮询面板读到半更新的不一致数字。
    # 可重入（RLock）以允许 _read_action_log 持锁时再调用 add_action，以及 to_detail_dict 调用 to_dict。
    # init=False/repr=False/compare=False：锁不参与构造、序列化与比较。
    _lock: "threading.RLock" = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    def add_action(self, action: AgentAction):
        """添加动作到最近动作列表"""
        # EXECPLAN2 F-6-1：持锁更新 recent_actions 与计数器，使其相对序列化原子。
        with self._lock:
            self.recent_actions.insert(0, action)
            if len(self.recent_actions) > self.max_recent_actions:
                self.recent_actions = self.recent_actions[:self.max_recent_actions]

            if action.platform == "twitter":
                self.twitter_actions_count += 1
            else:
                self.reddit_actions_count += 1

            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        # EXECPLAN2 F-6-1：持锁构造返回字典，确保所有标量计数器是一次性自洽快照。
        with self._lock:
            return self._to_dict_unlocked()

    def _to_dict_unlocked(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # 各平台独立轮次和时间
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
            "process_pgid": self.process_pgid,
            "twitter_enabled": self.twitter_enabled,
            "reddit_enabled": self.reddit_enabled,
            "graph_memory_requested": self.graph_memory_requested,
            "graph_memory_active": self.graph_memory_active,
            "graph_memory_error": self.graph_memory_error,
            "rounds_truncated_from": self.rounds_truncated_from,
            "rounds_truncated_to": self.rounds_truncated_to,
            "resumed_from_round": self.resumed_from_round,
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        """包含最近动作的详细信息"""
        # EXECPLAN2 F-6-1：持锁构造，使标量快照与 recent_actions 的拷贝相对监控线程一致，
        # 避免在请求线程序列化 recent_actions 的同时监控线程 insert/slice 造成不一致快照。
        with self._lock:
            result = self._to_dict_unlocked()
            result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
            result["rounds_count"] = len(self.rounds)
            return result


class SimulationRunner:
    """
    模拟运行器
    
    负责：
    1. 在后台进程中运行OASIS模拟
    2. 解析运行日志，记录每个Agent的动作
    3. 提供实时状态查询接口
    4. 支持停止操作与断点续跑（SIM_RESUME）；暂停/恢复未实现——
       RunnerStatus.PAUSED 仅为历史遗留状态值，代码从不主动进入（RUN-16）
    """
    
    # 运行状态存储目录
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )
    
    # 脚本目录
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # 内存中的运行状态
    _run_states: Dict[str, SimulationRunState] = {}
    # SIM-13: 每个模拟上次「落盘」run_state.json 的单调时刻，用于按
    # SIM_RUNSTATE_SAVE_INTERVAL 节流磁盘写入（内存态仍每次刷新）。
    _run_state_last_save: Dict[str, float] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}  # 存储 stdout 文件句柄
    _stderr_files: Dict[str, Any] = {}  # 存储 stderr 文件句柄
    
    # 图谱记忆更新配置
    _graph_memory_enabled: Dict[str, bool] = {}  # simulation_id -> enabled

    # 串行化 run_state.json / state.json 的读改写（EXECPLAN2 F-6-9）：
    # SimulationManager 也复用此锁（见 simulation_manager._save_simulation_state），
    # 让两个 writer 串行，避免基于陈旧快照的写覆盖刚落盘的状态。
    _run_state_lock: threading.RLock = threading.RLock()
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """获取运行状态"""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]
        
        # 尝试从文件加载
        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state

    @classmethod
    def join_monitor_thread(cls, simulation_id: str, timeout: float = 30.0) -> bool:
        """等待某模拟的监控线程退出（汇流栅栏用）。

        公共访问点：替代调用方直接读私有 ``_monitor_threads`` 注册表（EXECPLAN2 F-12-1
        的汇流栅栏 + NEXTSTEPS P0-3 的多种子集成都需要它）。监控线程的 finally 会停掉
        「模拟→图谱」反馈写入器并收尾 actions.jsonl，故 join 后再读图谱/run_summary 才稳。
        返回 True=线程已退出（或本就不存在）；False=超时仍存活。绝不抛出。
        """
        try:
            mon = cls._monitor_threads.get(simulation_id)
            if mon is None:
                return True
            if mon.is_alive():
                mon.join(timeout=timeout)
            return not mon.is_alive()
        except Exception:  # noqa: BLE001 — 栅栏为尽力而为，失败降级为告警由调用方处理
            return False

    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """从文件加载运行状态"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # 各平台独立轮次和时间
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
                process_pgid=data.get("process_pgid"),
                twitter_enabled=data.get("twitter_enabled", False),
                reddit_enabled=data.get("reddit_enabled", False),
                graph_memory_requested=data.get("graph_memory_requested", False),
                graph_memory_active=data.get("graph_memory_active", False),
                graph_memory_error=data.get("graph_memory_error"),
                rounds_truncated_from=data.get("rounds_truncated_from"),
                rounds_truncated_to=data.get("rounds_truncated_to"),
                resumed_from_round=data.get("resumed_from_round"),
            )
            
            # 加载最近动作
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"加载运行状态失败: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState, force: bool = False):
        """保存运行状态到文件（原子写入，避免实时端点读到半截 JSON，EXECPLAN2 F-7-6 主题）。

        SIM-13: 内存态（_run_states）始终立即刷新——get_run_state 返回的就是这同一个对象，
        故 API/编排轮询永远读到最新值；磁盘写入则按 SIM_RUNSTATE_SAVE_INTERVAL 秒节流，省掉
        监控线程每 2s 一次的无效 JSON 重写。终态（completed/failed/stopped）与 force=True 始终
        立即落盘，绝不丢终态。默认 interval=0 → 每次都写，与现状逐字节一致（degrade-safe）。
        """
        from ..utils.atomic import write_json_atomic
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")

        try:
            interval = float(getattr(Config, "SIM_RUNSTATE_SAVE_INTERVAL", 0) or 0)
        except (TypeError, ValueError):
            interval = 0.0
        terminal = state.runner_status in (
            RunnerStatus.COMPLETED, RunnerStatus.FAILED, RunnerStatus.STOPPED)

        with cls._run_state_lock:
            cls._run_states[state.simulation_id] = state  # 内存态始终最新
            now = time.monotonic()
            last = cls._run_state_last_save.get(state.simulation_id, 0.0)
            if force or terminal or interval <= 0 or (now - last) >= interval:
                write_json_atomic(state_file, state.to_detail_dict())
                cls._run_state_last_save[state.simulation_id] = now
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",  # twitter / reddit / parallel
        max_rounds: Optional[int] = None,  # 最大模拟轮数（T3.7: 默认 None=不截断，跑满按时长算出的完整轮数）
        enable_graph_memory_update: bool = False,  # 是否将活动更新到Zep图谱
        graph_id: Optional[str] = None,  # Zep图谱ID（启用图谱更新时必需）
        sim_seed: Optional[int] = None,  # NEXTSTEPS P0-3: 本次运行的确定性采样种子（仅注入子进程环境，不改全局）
        resume: Optional[bool] = None,  # RUN-7: True=显式续跑；None=由 SIM_RESUME 自动判定；False=强制全新
    ) -> SimulationRunState:
        """
        启动模拟
        
        Args:
            simulation_id: 模拟ID
            platform: 运行平台 (twitter/reddit/parallel)
            max_rounds: 最大模拟轮数（可选，用于截断过长的模拟）
            enable_graph_memory_update: 是否将Agent活动动态更新到Zep图谱
            graph_id: Zep图谱ID（启用图谱更新时必需）
            
        Returns:
            SimulationRunState
        """
        # 检查是否已在运行 —— 但状态须与「确有存活进程」交叉验证（EXECPLAN2 F-12-6）：
        # 重启/崩溃后 runner_status 可能仍持久化为 RUNNING，但本进程没有对应 Popen，
        # 此时应允许重跑而非永久拒绝。仅当确有存活进程时才视为「已在运行」。
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            if cls._is_simulation_alive(existing):
                raise ValueError(f"模拟已在运行中: {simulation_id}")
            logger.warning(
                f"模拟 {simulation_id} 持久化为 {existing.runner_status} 但无存活进程（重启遗留）→ 允许重跑"
            )
        
        # 加载模拟配置
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError(f"模拟配置不存在，请先调用 /prepare 接口")

        # RUN-7: 断点续跑（默认关，SIM_RESUME=true 开启）。resume=None 时自动判定：
        # 仅当上次运行未 COMPLETED 且存在轮级检查点才续跑（COMPLETED 后的重跑视为要全新结果）。
        # 续跑时保留 actions.jsonl / 模拟 DB / 检查点，让子进程从上次完成的轮次继续。
        sim_resume_flag = bool(getattr(Config, "SIM_RESUME", False))
        if resume is None:
            resume_requested = (
                sim_resume_flag
                and existing is not None
                and existing.runner_status != RunnerStatus.COMPLETED
            )
        else:
            resume_requested = bool(resume)
        # 仅 parallel 脚本支持 --resume/检查点；单平台脚本（run_twitter/reddit_simulation.py）
        # 的 argparse 不认识该参数，传入会直接 exit(2)。
        if resume_requested and platform != "parallel":
            logger.info(f"[{simulation_id}] 单平台运行不支持续跑（{platform}）→ 全新运行")
            resume_requested = False
        resume_from_round = cls._resume_checkpoint_round(sim_dir) if resume_requested else None
        resume_active = resume_from_round is not None
        if resume_requested and not resume_active:
            logger.info(f"[{simulation_id}] 请求续跑但无可用检查点 → 全新运行")

        if not resume_active:
            cls._rotate_stale_action_logs(sim_dir)
        else:
            logger.info(f"[{simulation_id}] 断点续跑：保留动作日志与模拟 DB，从第 {resume_from_round} 轮之后继续")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 初始化运行状态
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        # 与配置生成器默认值(60)保持一致：缺省字段时算出 72 轮而非 144 轮，消除潜在的 2x 差异。
        minutes_per_round = time_config.get("minutes_per_round", 60)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # T3.7: 仅当显式给定 max_rounds 时才截断（默认 None=跑满）。截断时把「本应/实际」记为一等字段。
        truncated_from = None
        truncated_to = None
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                truncated_from, truncated_to = original_rounds, total_rounds
                logger.info(f"轮数已截断: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
            rounds_truncated_from=truncated_from,
            rounds_truncated_to=truncated_to,
            resumed_from_round=resume_from_round if resume_active else None,
        )
        
        cls._save_run_state(state)
        
        # 如果启用图谱记忆更新，创建更新器（记录「请求 vs 实际」，避免谎报，F-6-12）
        state.graph_memory_requested = bool(enable_graph_memory_update)
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("启用图谱记忆更新时必须提供 graph_id")

            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                state.graph_memory_active = True
                logger.info(f"已启用图谱记忆更新: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"创建图谱记忆更新器失败: {e}")
                cls._graph_memory_enabled[simulation_id] = False
                state.graph_memory_active = False
                state.graph_memory_error = str(e)
        else:
            cls._graph_memory_enabled[simulation_id] = False
            state.graph_memory_active = False

        # 确定运行哪个脚本（脚本位于 backend/scripts/ 目录）
        # twitter_enabled/reddit_enabled 为权威启用来源（持久化），供完成判定使用（F-6-10）。
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
            state.twitter_enabled = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
            state.reddit_enabled = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
            state.twitter_enabled = True
            state.reddit_enabled = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"脚本不存在: {script_path}")
        
        # 创建动作队列
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # 启动模拟进程
        try:
            # 构建运行命令，使用完整路径
            # 新的日志结构：
            #   twitter/actions.jsonl - Twitter 动作日志
            #   reddit/actions.jsonl  - Reddit 动作日志
            #   simulation.log        - 主进程日志
            
            cmd = [
                sys.executable,  # Python解释器
                script_path,
                "--config", config_path,  # 使用完整配置文件路径
            ]
            
            # 如果指定了最大轮数，添加到命令行参数
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])

            # RUN-7: 续跑标志——子进程据此保留 DB、跳过种子注入并从检查点轮次继续。
            if resume_active:
                cmd.append("--resume")
            
            # 创建主日志文件，避免 stdout/stderr 管道缓冲区满导致进程阻塞
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # 设置子进程环境变量，确保 Windows 上使用 UTF-8 编码
            # 这可以修复第三方库（如 OASIS）读取文件时未指定编码的问题
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'  # Python 3.7+ 支持，让所有 open() 默认使用 UTF-8
            env['PYTHONIOENCODING'] = 'utf-8'  # 确保 stdout/stderr 使用 UTF-8
            # NEXTSTEPS P0-3: 把本次运行的采样种子**仅注入子进程环境**（run_parallel_simulation
            # 优先读 env['SIM_SEED']），不触碰本进程全局 os.environ，避免并发管线相互污染。
            if sim_seed is not None:
                env['SIM_SEED'] = str(int(sim_seed))
            # RUN-7: 开启 SIM_RESUME（或显式续跑）时向子进程注入开关——子进程据此每轮
            # 原子落盘 checkpoint.json，使后续崩溃/重启可以续跑而非从第 0 轮重烧额度。
            if sim_resume_flag or resume_active:
                env['SIM_RESUME'] = 'true'

            # 设置工作目录为模拟目录（数据库等文件会生成在此）
            # 使用 start_new_session=True 创建新的进程组，确保可以通过 os.killpg 终止所有子进程
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,  # stderr 也写入同一个文件
                text=True,
                encoding='utf-8',  # 显式指定编码
                bufsize=1,
                env=env,  # 传递带有 UTF-8 设置的环境变量
                start_new_session=True,  # 创建新进程组，确保服务器关闭时能终止所有相关进程
            )
            
            # 保存文件句柄以便后续关闭
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None  # 不再需要单独的 stderr
            
            state.process_pid = process.pid
            # 记录进程组 id（start_new_session=True 时 pgid==pid），供重启后按 pgid 回收孤儿。
            try:
                state.process_pgid = os.getpgid(process.pid) if not IS_WINDOWS else None
            except OSError:
                state.process_pgid = None
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # 启动监控线程
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"模拟启动成功: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """监控模拟进程，解析动作日志"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # 新的日志结构：分平台的动作日志
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:  # 进程仍在运行
                # 读取 Twitter 动作日志
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # 读取 Reddit 动作日志
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # 更新状态
                cls._save_run_state(state)
                time.sleep(2)
            
            # 进程结束后，最后读取一次日志
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # 进程结束
            exit_code = process.returncode
            
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.completed_at = datetime.now().isoformat()
                # RUN-17: 平台在进入主循环前中止（如 profile 缺失）时子进程仍会 0 退出——
                # 交叉核验各启用平台是否都发出了 simulation_end，未齐则把退化记入 error，
                # 让管线健康门/UI 看见「COMPLETED 但部分平台未产出」的自相矛盾终态。
                if not cls._check_all_platforms_completed(state):
                    state.error = "部分平台未产出 simulation_end（可能启动失败或中途异常被隔离）"
                    logger.warning(f"模拟完成但存在未完成平台: {simulation_id}")
                logger.info(f"模拟完成: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # 从主日志文件读取错误信息
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]  # 取最后2000字符
                except Exception:
                    pass
                state.error = f"进程退出码: {exit_code}, 错误: {error_info}"
                logger.error(f"模拟失败: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            # RUN-11: 子进程已整体退出（含崩溃/被杀），IPC 必然不可用——把 env_status.json
            # 落为 stopped，防止 interview 端点对着死进程阻塞整个超时窗。优雅退出时子进程
            # 自己已写 stopped，重复写幂等无害。
            cls._mark_env_stopped(simulation_id)

        except Exception as e:
            logger.error(f"监控线程异常: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # 停止图谱记忆更新器
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"已停止图谱记忆更新: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"停止图谱记忆更新器失败: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # 清理进程资源
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # 关闭日志文件句柄
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        读取动作日志文件
        
        Args:
            log_path: 日志文件路径
            position: 上次读取位置
            state: 运行状态对象
            platform: 平台名称 (twitter/reddit)
            
        Returns:
            新的读取位置
        """
        # 检查是否启用了图谱记忆更新
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # 处理事件类型的条目
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")

                                # 检测 simulation_end 事件，标记平台已完成
                                # EXECPLAN2 F-6-1：持锁更新标量状态，使其相对请求线程的序列化自洽。
                                if event_type == "simulation_end":
                                    with state._lock:
                                        if platform == "twitter":
                                            state.twitter_completed = True
                                            state.twitter_running = False
                                            logger.info(f"Twitter 模拟已完成: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                        elif platform == "reddit":
                                            state.reddit_completed = True
                                            state.reddit_running = False
                                            logger.info(f"Reddit 模拟已完成: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")

                                        # 检查是否所有启用的平台都已完成
                                        # 如果只运行了一个平台，只检查那个平台
                                        # 如果运行了两个平台，需要两个都完成
                                        all_completed = cls._check_all_platforms_completed(state)
                                        if all_completed:
                                            state.runner_status = RunnerStatus.COMPLETED
                                            state.completed_at = datetime.now().isoformat()
                                            logger.info(f"所有平台模拟已完成: {state.simulation_id}")

                                # 更新轮次信息（从 round_end 事件）
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)

                                    # EXECPLAN2 F-6-1：轮次/模拟时间标量的更新同样持锁。
                                    with state._lock:
                                        # 更新各平台独立的轮次和时间
                                        if platform == "twitter":
                                            if round_num > state.twitter_current_round:
                                                state.twitter_current_round = round_num
                                            state.twitter_simulated_hours = simulated_hours
                                        elif platform == "reddit":
                                            if round_num > state.reddit_current_round:
                                                state.reddit_current_round = round_num
                                            state.reddit_simulated_hours = simulated_hours

                                        # 总体轮次取两个平台的最大值
                                        if round_num > state.current_round:
                                            state.current_round = round_num
                                        # 总体时间取两个平台的最大值
                                        state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)

                                continue

                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            # EXECPLAN2 F-6-1：add_action 已内部持锁；紧随其后的 current_round
                            # 更新也持同一把可重入锁，保证计数器与轮次的快照一致。
                            with state._lock:
                                state.add_action(action)
                                # 更新轮次
                                if action.round_num and action.round_num > state.current_round:
                                    state.current_round = action.round_num

                            # 如果启用了图谱记忆更新，将活动发送到Zep
                            # （网络/IO 副作用，放在锁外以缩短临界区）
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"读取动作日志失败: {log_path}, error={e}")
            return position
    
    # RUN-15: 重跑前须一并轮转的派生产物——若上一轮的 run_summary / 世界态轨迹 / 决策流 /
    # 涌现度量存活到新一轮且新一轮在再生它们之前失败，报告阶段会把「上一次运行」的
    # 产物当作本次运行的结果静默消费。
    _STALE_DERIVED_ARTIFACTS = (
        "run_summary.json",
        "world_state_trajectory.json",
        "decisions.jsonl",
        "emergent_metrics.json",
        "twitter_emergent_metrics.json",
        "reddit_emergent_metrics.json",
        "twitter_dynamics_summary.json",
        "reddit_dynamics_summary.json",
        "ipc_telemetry.jsonl",
        "llm_health.json",
        "llm_fallback.jsonl",
    )

    @classmethod
    def _rotate_stale_action_logs(cls, sim_dir: str) -> None:
        """重跑同一 simulation 前轮转上一轮的动作日志与派生产物。

        actions.jsonl 由模拟脚本以 append 模式写入（模拟 DB 会被脚本自删重建，
        但动作日志不会）。管线 resume 失败的 run 阶段时若不清理，旧轮次动作会
        混进新一轮的进度监控、帖子流和报告分析。轮转为 *.prev 保留现场便于排查
        （只保留最近一份）。RUN-15: 派生产物（run_summary 等）同样轮转，防止
        新一轮失败时报告阶段消费到上一轮的摘要/轨迹。
        """
        # 轮转目标名保持既有约定：actions.jsonl -> actions.prev.jsonl（下游排查工具已依赖）。
        for plat in ("twitter", "reddit"):
            for name, prev_name in (("actions.jsonl", "actions.prev.jsonl"),
                                    ("checkpoint.json", "checkpoint.prev.json")):
                stale = os.path.join(sim_dir, plat, name)
                if os.path.exists(stale):
                    try:
                        os.replace(stale, os.path.join(sim_dir, plat, prev_name))
                        logger.info(f"已轮转上一轮文件: {plat}/{name} -> {prev_name}")
                    except OSError as e:
                        logger.warning(f"轮转旧文件失败（{stale}）: {e}")
        for name in cls._STALE_DERIVED_ARTIFACTS:
            stale = os.path.join(sim_dir, name)
            if os.path.exists(stale):
                try:
                    os.replace(stale, os.path.join(sim_dir, f"{name}.prev"))
                    logger.info(f"已轮转上一轮派生产物: {name} -> {name}.prev")
                except OSError as e:
                    logger.warning(f"轮转旧派生产物失败（{stale}）: {e}")

    @classmethod
    def _current_config_hash(cls, sim_dir: str) -> str:
        """ITEM 3: 计算当前 simulation_config.json 的稳定指纹（与脚本侧 run_parallel_simulation
        ._config_hash 同法：整份 config 规范化 JSON 的 sha256）。用于续跑决策时校验配置未变；
        文件缺失/读失败/异常一律返回 ""（视为无指纹 → 不阻断续跑，向后兼容）。"""
        import hashlib
        cfg_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(cfg_path):
            return ""
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            blob = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
            return hashlib.sha256(blob.encode("utf-8")).hexdigest()
        except (OSError, ValueError, TypeError):
            return ""

    @classmethod
    def _resume_checkpoint_round(cls, sim_dir: str) -> Optional[int]:
        """RUN-7: 读取平台轮级检查点，返回可续跑的已完成轮次（无有效检查点 → None）。

        取各平台 completed_round 的最大值（进度以最快的平台记账；慢平台的检查点由
        子进程各自读取）。检查点损坏/字段非法一律视为不可续跑，绝不抛出。

        ITEM 3: 若检查点带 config_hash 且与当前 simulation_config.json 指纹不符（配置已变更），
        则该平台检查点不可续跑（跳过，不计入 best）——避免把旧世界续进新意图。检查点无 config_hash
        或当前配置读不到时不阻断（向后兼容旧检查点）。脚本侧在续跑时还会二次校验，构成纵深防御。
        """
        cur_hash = cls._current_config_hash(sim_dir)
        best: Optional[int] = None
        for plat in ("twitter", "reddit"):
            ckpt_path = os.path.join(sim_dir, plat, "checkpoint.json")
            if not os.path.exists(ckpt_path):
                continue
            try:
                with open(ckpt_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                completed = int(data.get("completed_round", 0))
                saved_hash = str(data.get("config_hash", "") or "")
                # ITEM 3: config 指纹存在且不匹配 → 该平台不可续跑（配置已变更）
                if saved_hash and cur_hash and saved_hash != cur_hash:
                    continue
                if completed >= 1 and (best is None or completed > best):
                    best = completed
            except (OSError, ValueError, TypeError):
                continue
        return best

    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """
        检查所有启用的平台是否都已完成模拟。

        权威启用来源是启动时写定并持久化的 state.twitter_enabled/reddit_enabled
        （EXECPLAN2 F-6-10）。旧逻辑用 actions.jsonl 是否存在来推断启用，会在启动早期
        （文件尚未生成）或某平台早期失败时把它当作「未启用」而过早判定 COMPLETED。

        Returns:
            True 如果所有启用的平台都已完成
        """
        twitter_enabled = state.twitter_enabled
        reddit_enabled = state.reddit_enabled

        # 向后兼容：极旧的 run_state.json 没有 *_enabled 字段（均为 False）时，
        # 回退到原先的「文件存在即启用」启发式，避免误判历史运行。
        if not twitter_enabled and not reddit_enabled:
            sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
            twitter_enabled = os.path.exists(os.path.join(sim_dir, "twitter", "actions.jsonl"))
            reddit_enabled = os.path.exists(os.path.join(sim_dir, "reddit", "actions.jsonl"))

        # 任一启用平台未完成 → 整体未完成
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False

        # 至少有一个平台被启用且已完成
        return twitter_enabled or reddit_enabled
    
    # 启动脚本名（用于按 PID 反查命令行、确认身份，防 PID 复用误杀）
    _RUN_SCRIPT_NAMES = (
        "run_parallel_simulation.py",
        "run_twitter_simulation.py",
        "run_reddit_simulation.py",
    )

    @classmethod
    def _orphan_cmdline(cls, pid: int) -> Optional[str]:
        """返回 pid 的命令行（用于身份校验）；进程不存在/无法查询时返回 None。"""
        if IS_WINDOWS:
            return None  # Windows 无 ps；身份校验降级（见 _kill_orphan_simulation）
        try:
            check = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5,
            )
            if check.returncode != 0:
                return None
            return (check.stdout or "").strip()
        except (subprocess.SubprocessError, OSError):
            return None

    @classmethod
    def _pid_is_our_simulation(cls, pid: int, simulation_id: str) -> bool:
        """确认 pid 确为本 simulation 的运行脚本（命令行含运行脚本名 + simulation_id）。"""
        cmdline = cls._orphan_cmdline(pid)
        if cmdline is None:
            # 无法取命令行：在 Unix 上视为不可确认（保守不杀）；
            if not IS_WINDOWS:
                return False
            # Windows：退化为存活性判断
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError, OSError):
                return False
        return any(s in cmdline for s in cls._RUN_SCRIPT_NAMES) and (simulation_id in cmdline)

    @classmethod
    def _is_simulation_alive(cls, state: "SimulationRunState") -> bool:
        """判断该模拟是否确有存活进程（本进程的 Popen 或持久化 PID 仍在跑且身份匹配）。"""
        proc = cls._processes.get(state.simulation_id)
        if proc is not None and proc.poll() is None:
            return True
        pid = state.process_pid
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        # 存活，但须确认是同一个模拟进程（防 PID 复用）
        return cls._pid_is_our_simulation(int(pid), state.simulation_id)

    @classmethod
    def _kill_orphan_simulation(cls, state: "SimulationRunState") -> bool:
        """终止上一后端进程遗留、仍在烧额度的孤儿模拟进程组（按持久化 pid/pgid）。

        返回 True 表示「进程已不在」（被杀或本就不存在）；False 表示仍可能存活。
        谨慎校验命令行身份，防 PID 复用误杀（与 _kill_orphan_research 同构，F-6-5/F-12-0）。
        """
        pid = state.process_pid
        if not pid:
            return True
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return True
        # 进程是否还在？
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return True  # 已退出
        # 在则先确认身份，不匹配（PID 已复用）→ 不动它，但视为孤儿已消失
        if not cls._pid_is_our_simulation(pid, state.simulation_id):
            return True
        try:
            if IS_WINDOWS:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=8)
            else:
                pgid = state.process_pgid or os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                # 给一点时间优雅退出，再确认
                time.sleep(1.0)
                try:
                    os.kill(pid, 0)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            logger.warning(f"[{state.simulation_id}] 已终止孤儿模拟进程组 pid={pid}")
        except (ProcessLookupError, PermissionError, OSError) as e:
            logger.warning(f"[{state.simulation_id}] 终止孤儿模拟失败（可能已退出）: {e}")
        # 复核
        try:
            os.kill(pid, 0)
            return False  # 仍存活（少见）
        except (ProcessLookupError, PermissionError, OSError):
            return True

    @classmethod
    def reconcile_orphans(cls) -> None:
        """后端启动时回收上一进程遗留的孤儿模拟（EXECPLAN2 F-12-0/F-6-5/F-12-6）。

        新进程的 ``_processes`` 必为空，故任何持久化为活跃状态（RUNNING/STARTING/STOPPING/
        PAUSED）的模拟都是孤儿：杀掉其仍在烧额度的进程组，并把 run_state.json + state.json
        落为终态，让前端轮询停下、并允许重跑。整个过程 try/except 包裹，绝不阻断启动。
        """
        active = {RunnerStatus.RUNNING, RunnerStatus.STARTING, RunnerStatus.STOPPING, RunnerStatus.PAUSED}
        try:
            if not os.path.isdir(cls.RUN_STATE_DIR):
                return
            for sim_id in os.listdir(cls.RUN_STATE_DIR):
                try:
                    if sim_id in cls._processes:
                        continue  # 本进程自己起的，不是孤儿
                    state = cls._load_run_state(sim_id)
                    if not state or state.runner_status not in active:
                        continue
                    cls._kill_orphan_simulation(state)
                    state.runner_status = RunnerStatus.FAILED
                    state.twitter_running = False
                    state.reddit_running = False
                    state.completed_at = datetime.now().isoformat()
                    state.error = "后端在运行中被中断（进程重启），该模拟已回收为失败。"
                    cls._save_run_state(state)
                    cls._sync_state_json_status(sim_id, "failed")
                    cls._mark_env_stopped(sim_id)  # RUN-11: 孤儿已回收，IPC 环境必然失效
                    logger.warning(f"[{sim_id}] 启动时回收孤儿模拟 → failed")
                except Exception as e:  # noqa: BLE001 — 单条回收失败不应影响其它
                    logger.error(f"回收孤儿模拟失败 ({sim_id}): {e}")
        except Exception as e:  # noqa: BLE001 — 回收失败不应阻断启动
            logger.error(f"回收孤儿模拟总流程失败: {e}", exc_info=True)

    @classmethod
    def _mark_env_stopped(cls, simulation_id: str) -> None:
        """RUN-11: 把 env_status.json 原子落为 stopped（best-effort，绝不抛出）。

        env_status.json 原本只由模拟子进程在优雅退出时写 stopped；进程崩溃/被 SIGKILL/
        被 stop·reconcile·cleanup 回收后它永远停留在 alive，check_env_alive() 恒真——
        interview 端点会向不存在的进程发 IPC 并阻塞完整的 60-180s 超时窗。凡在后端侧
        确认进程终止，就同步落 stopped。目录不存在（模拟从未运行/已清理）时不产生新文件。
        """
        from ..utils.atomic import write_json_atomic
        try:
            sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
            if not os.path.isdir(sim_dir):
                return
            write_json_atomic(os.path.join(sim_dir, "env_status.json"), {
                "status": "stopped",
                "twitter_available": False,
                "reddit_available": False,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:  # noqa: BLE001 — 状态标记失败不应影响终止流程本身
            logger.warning(f"标记 env_status=stopped 失败: {simulation_id}, error={e}")

    @classmethod
    def _sync_state_json_status(cls, simulation_id: str, status: str) -> None:
        """把 state.json 的 status 原子更新为终态，与 run_state 保持一致（复用共享锁，F-6-9）。"""
        from ..utils.atomic import write_json_atomic
        try:
            state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "state.json")
            if not os.path.exists(state_file):
                return
            with cls._run_state_lock:
                with open(state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['status'] = status
                data['updated_at'] = datetime.now().isoformat()
                write_json_atomic(state_file, data)
        except Exception as e:
            logger.warning(f"更新 state.json 状态失败: {simulation_id}, error={e}")

    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """
        跨平台终止进程及其子进程
        
        Args:
            process: 要终止的进程
            simulation_id: 模拟ID（用于日志）
            timeout: 等待进程退出的超时时间（秒）
        """
        if IS_WINDOWS:
            # Windows: 使用 taskkill 命令终止进程树
            # /F = 强制终止, /T = 终止进程树（包括子进程）
            logger.info(f"终止进程树 (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # 先尝试优雅终止
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # 强制终止
                    logger.warning(f"进程未响应，强制终止: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill 失败，尝试 terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: 使用进程组终止
            # 由于使用了 start_new_session=True，进程组 ID 等于主进程 PID
            pgid = os.getpgid(process.pid)
            logger.info(f"终止进程组 (Unix): simulation={simulation_id}, pgid={pgid}")
            
            # 先发送 SIGTERM 给整个进程组
            os.killpg(pgid, signal.SIGTERM)
            
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 如果超时后还没结束，强制发送 SIGKILL
                logger.warning(f"进程组未响应 SIGTERM，强制终止: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """停止模拟"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        # RUN-16: 允许停止 STARTING——启动阶段卡死（如 agent 建图挂在 LLM 上）的模拟
        # 此前必须等它自己转为 RUNNING 才能通过 API 停止；终止路径本就能处理短命/缺失进程。
        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED, RunnerStatus.STARTING]:
            raise ValueError(f"模拟未在运行: {simulation_id}, status={state.runner_status}")
        
        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)

        # 终止进程 —— STOPPED 必须与「确实终止了进程」绑定（EXECPLAN2 F-6-11）。
        terminated = False
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
                terminated = True
            except ProcessLookupError:
                terminated = True  # 进程已经不存在
            except Exception as e:
                logger.error(f"终止进程组失败: {simulation_id}, error={e}")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                    terminated = True
                except Exception:
                    try:
                        process.kill()
                        terminated = True
                    except Exception:
                        terminated = False
        elif process is not None:
            # 有句柄但已退出
            terminated = True
        else:
            # 本进程没有句柄（重启遗留的孤儿）：按持久化 pid 杀进程组
            terminated = cls._kill_orphan_simulation(state)

        if terminated:
            state.runner_status = RunnerStatus.STOPPED
            state.twitter_running = False
            state.reddit_running = False
            state.completed_at = datetime.now().isoformat()
            cls._mark_env_stopped(simulation_id)  # RUN-11: 进程已确认终止，环境状态同步落 stopped
        else:
            # 没能确认终止 —— 不要谎报 STOPPED
            state.runner_status = RunnerStatus.FAILED
            state.error = "停止失败：进程仍可能存活（无法确认终止）。"
            logger.error(f"停止失败，进程可能仍存活: {simulation_id}")
        cls._save_run_state(state)
        
        # 停止图谱记忆更新器
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"已停止图谱记忆更新: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"停止图谱记忆更新器失败: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)
        
        logger.info(f"模拟已停止: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        从单个动作文件中读取动作
        
        Args:
            file_path: 动作日志文件路径
            default_platform: 默认平台（当动作记录中没有 platform 字段时使用）
            platform_filter: 过滤平台
            agent_id: 过滤 Agent ID
            round_num: 过滤轮次
        """
        if not os.path.exists(file_path):
            return []
        
        actions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 跳过非动作记录（如 simulation_start, round_start, round_end 等事件）
                    if "event_type" in data:
                        continue
                    
                    # 跳过没有 agent_id 的记录（非 Agent 动作）
                    if "agent_id" not in data:
                        continue
                    
                    # 获取平台：优先使用记录中的 platform，否则使用默认平台
                    record_platform = data.get("platform") or default_platform or ""
                    
                    # 过滤
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        获取所有平台的完整动作历史（无分页限制）
        
        Args:
            simulation_id: 模拟ID
            platform: 过滤平台（twitter/reddit）
            agent_id: 过滤Agent
            round_num: 过滤轮次
            
        Returns:
            完整的动作列表（按时间戳排序，新的在前）
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []
        
        # 读取 Twitter 动作文件（根据文件路径自动设置 platform 为 twitter）
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # 自动填充 platform 字段
                platform_filter=platform,
                agent_id=agent_id, 
                round_num=round_num
            ))
        
        # 读取 Reddit 动作文件（根据文件路径自动设置 platform 为 reddit）
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # 自动填充 platform 字段
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))
        
        # 如果分平台文件不存在，尝试读取旧的单一文件格式
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # 旧格式文件中应该有 platform 字段
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )
        
        # 按时间戳排序（新的在前）
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        获取动作历史（带分页）
        
        Args:
            simulation_id: 模拟ID
            limit: 返回数量限制
            offset: 偏移量
            platform: 过滤平台
            agent_id: 过滤Agent
            round_num: 过滤轮次
            
        Returns:
            动作列表
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        # 分页
        return actions[offset:offset + limit]
    
    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取模拟时间线（按轮次汇总）
        
        Args:
            simulation_id: 模拟ID
            start_round: 起始轮次
            end_round: 结束轮次
            
        Returns:
            每轮的汇总信息
        """
        # EXECPLAN2 F-6-3：聚合须读取完整动作历史，不能用 get_actions(limit=10000) 的分页切片
        # （会按时间倒序丢弃最早的轮次），否则长时模拟的早期轮次会从时间线中消失。
        actions = cls.get_all_actions(simulation_id)

        # 按轮次分组
        rounds: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            round_num = action.round_num
            
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue
            
            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            r = rounds[round_num]
            
            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1
            
            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            # XRUN-9: 合并后的 twitter+reddit 动作流并非时间有序，必须比较时间戳取 min/max，
            # 否则 first/last 反转（ISO-8601 字符串可安全按字典序比较；空串不得覆盖有效值）。
            if action.timestamp:
                if not r["first_action_time"] or action.timestamp < r["first_action_time"]:
                    r["first_action_time"] = action.timestamp
                if not r["last_action_time"] or action.timestamp > r["last_action_time"]:
                    r["last_action_time"] = action.timestamp
        
        # 转换为列表
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        
        return result
    
    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        获取每个Agent的统计信息
        
        Returns:
            Agent统计列表
        """
        # EXECPLAN2 F-6-3：per-agent 统计须基于完整历史，否则超过 1w 动作后早期轮次/Agent 会被截断、
        # engagement 总量被低估。
        actions = cls.get_all_actions(simulation_id)

        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            # XRUN-9: 同 get_timeline——比较时间戳而非按遍历顺序覆盖，修复 first>last 反转。
            if action.timestamp:
                if not stats["first_action_time"] or action.timestamp < stats["first_action_time"]:
                    stats["first_action_time"] = action.timestamp
                if not stats["last_action_time"] or action.timestamp > stats["last_action_time"]:
                    stats["last_action_time"] = action.timestamp
        
        # 按总动作数排序
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)

        return result

    @classmethod
    def write_run_summary(
        cls,
        simulation_id: str,
        communities: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """T3.14: 聚合 actions.jsonl + 时间线，落 run_summary.json（报告阶段直接读，免再 fuzzy 检索）。

        产出：per-agent engagement（沿用 get_agent_stats）、action_volume_by_round（来自 get_timeline）、
        top_posts（按互动量近似的高传播帖）、可选 communities（派系，来自 T2.4）。
        best-effort：任何子步骤失败都不抛，返回已聚合的部分（或 None）。
        """
        try:
            agent_stats = cls.get_agent_stats(simulation_id)
            timeline = cls.get_timeline(simulation_id)
            # EXECPLAN2 F-6-3：run_summary 喂给报告/预测阶段，必须基于完整动作历史，
            # 否则 total_actions / top_posts / rounds_executed 会因 1w 截断而偏向 late-run 窗口。
            actions = cls.get_all_actions(simulation_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{simulation_id}] run_summary 聚合失败: {e}")
            return None

        # 每轮动作量
        action_volume_by_round = [
            {
                "round_num": r["round_num"],
                "total_actions": r["total_actions"],
                "active_agents_count": r["active_agents_count"],
                "action_types": r["action_types"],
            }
            for r in timeline
        ]

        # top_posts（RUN-13）：actions.jsonl 没有互动列（在 DB 里），无法真正按传播量排序。
        # 旧实现取时间倒序前 25 条（= 最新帖，且混入 round-0 种子帖）却自称按互动排序。
        # 诚实的最小版本：剔除 round-0 种子帖与定时回放事件，按 (轮次, 时间) 升序取
        # agent 自发帖样本——报告端拿到的是「有机帖子的时间序样本」而非倒序尾巴。
        organic_posts = []
        for a in actions:
            if a.action_type not in ("CREATE_POST", "QUOTE_POST"):
                continue
            args = a.action_args or {}
            if (a.round_num or 0) <= 0 or args.get("is_scheduled_event"):
                continue  # 种子注入/时间线回放不是 agent 的自发行为
            try:
                content = str(args.get("content", ""))
            except Exception:
                content = ""
            organic_posts.append({
                "round_num": a.round_num,
                "agent_id": a.agent_id,
                "agent_name": a.agent_name,
                "platform": a.platform,
                "content": content[:280],
                "timestamp": a.timestamp,
            })
        organic_posts.sort(key=lambda p: (p["round_num"], p["timestamp"] or ""))
        top_posts = [{k: v for k, v in p.items() if k != "timestamp"} for p in organic_posts[:25]]

        peak = max(action_volume_by_round, key=lambda x: x["total_actions"], default=None)

        # QUALITY-OPT S4/C3: honest run accounting. Distinguish ORGANIC engagement (posts/
        # comments/likes the agents chose to make) from SEED graph actions (sign-up/follow), so a
        # simulation whose 554 "actions" were all seed sign-ups is not mistaken for a lively run.
        # rounds_executed comes from run_state.current_round (real rounds ran), not from the count
        # of distinct round_nums that happened to carry an action (which collapses to 1 for a
        # seed-only run). simulation_health is consumed by the pipeline health gate + report caveat.
        _ORGANIC_TYPES = {"CREATE_POST", "QUOTE_POST", "REPOST", "CREATE_COMMENT",
                          "LIKE_POST", "LIKE_COMMENT", "DISLIKE_POST", "DISLIKE_COMMENT"}
        # XRUN-2(2): round-0 的种子帖与带 is_scheduled_event 标记的时间线回放是注入而非
        # agent 决策——不得计入有机量，否则纯种子运行会被误判为 lively（hollow 检测失灵）。
        organic = [
            a for a in actions
            if str(getattr(a, "action_type", "") or "").upper() in _ORGANIC_TYPES
            and (a.round_num or 0) > 0
            and not (a.action_args or {}).get("is_scheduled_event")
        ]
        organic_count = len(organic)
        seed_count = max(0, len(actions) - organic_count)
        rounds_with_organic = len({a.round_num for a in organic})
        # real round count + error/truncation from run_state.json
        current_round = total_rounds = None
        run_error = None
        try:
            _rsp = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
            if os.path.exists(_rsp):
                with open(_rsp, encoding="utf-8") as _f:
                    _rs = json.load(_f)
                current_round = _rs.get("current_round")
                total_rounds = _rs.get("total_rounds") or _rs.get("total_simulation_rounds")
                run_error = _rs.get("error")
        except (OSError, ValueError):
            pass
        rounds_executed = current_round if isinstance(current_round, int) else len(action_volume_by_round)
        truncated = bool(isinstance(current_round, int) and isinstance(total_rounds, int)
                         and total_rounds > 0 and current_round < total_rounds)
        if run_error:
            sim_health = "errored"
        elif organic_count == 0:
            sim_health = "hollow"        # agents produced NO organic content — do not narrativize
        elif truncated:
            sim_health = "truncated"
        else:
            sim_health = "ok"

        # RUN-2: 运行环落盘的平台级 LLM 健康（llm_health.json）。任一平台 degraded=true 时把
        # ok 降为 llm_degraded（errored/hollow/truncated 语义更强者不被覆盖）。老运行无此文件 → 行为不变。
        llm_health = None
        try:
            _lhp = os.path.join(cls.RUN_STATE_DIR, simulation_id, "llm_health.json")
            if os.path.exists(_lhp):
                with open(_lhp, encoding="utf-8") as _lf:
                    llm_health = json.load(_lf)
                _plats = (llm_health or {}).get("platforms") or {}
                if sim_health == "ok" and any(
                        isinstance(_p, dict) and _p.get("degraded") for _p in _plats.values()):
                    sim_health = "llm_degraded"
        except (OSError, ValueError):
            llm_health = None

        # RUN-9: 情感动态观测摘要（{platform}_dynamics_summary.json）。active=false 时报告阶段
        # 据此抑制「情绪演化」叙事（动态信号从未送达 agent 提示词）。缺失 → 不写该键。
        agent_dynamics = {}
        for _plat in ("twitter", "reddit"):
            try:
                _dsp = os.path.join(cls.RUN_STATE_DIR, simulation_id, f"{_plat}_dynamics_summary.json")
                if os.path.exists(_dsp):
                    with open(_dsp, encoding="utf-8") as _df:
                        _ds = json.load(_df)
                    if isinstance(_ds, dict):
                        agent_dynamics[_plat] = _ds
            except (OSError, ValueError):
                continue

        # ITEM 20 (SIMULATED_HOURS 记账): run_summary 历史上从不落 simulated_hours（round_end 事件
        # 的累计值没回传到这里），报告读到恒 0 的模拟时长。这里从 simulation_config.json 读
        # minutes_per_round，据唯一权威公式 rounds_executed × minutes_per_round / 60 重算。
        _minutes_per_round = 60.0
        try:
            _cfgp = os.path.join(cls.RUN_STATE_DIR, simulation_id, "simulation_config.json")
            if os.path.exists(_cfgp):
                with open(_cfgp, encoding="utf-8") as _cf:
                    _sc = json.load(_cf)
                _mpr = (_sc.get("time_config") or {}).get("minutes_per_round", 60)
                _minutes_per_round = float(_mpr) if _mpr else 60.0
        except (OSError, ValueError, TypeError):
            _minutes_per_round = 60.0
        try:
            from app.services.agent_dynamics import simulated_hours_from_rounds
            simulated_hours = simulated_hours_from_rounds(rounds_executed, _minutes_per_round)
        except Exception:  # noqa: BLE001 — 记账辅助失败退回 0.0（degrade-safe）
            simulated_hours = 0.0

        # ITEM 20 (SIM_ORGANIC_RATIO_DETECTOR): 逐平台逐轮 post:comment:like 比例塌缩侦测。只用
        # agent 自发的有机动作，显式排除 is_engagement_sample（采样赞）——诚实优先：采样赞不得
        # 掩盖 agent 自身零点赞的塌缩。连续 ≥K 轮 posts>0 而 comments+likes==0 → 结构化告警。
        organic_ratio_warnings: List[Dict[str, Any]] = []
        try:
            from app.config import Config
            if getattr(Config, "SIM_ORGANIC_RATIO_DETECTOR", True):
                from app.services.agent_dynamics import (
                    classify_organic_action, detect_organic_ratio_collapse,
                )
                _prc: Dict[str, Dict[int, Dict[str, int]]] = {}
                for a in organic:
                    if (a.action_args or {}).get("is_engagement_sample"):
                        continue  # 采样赞不计入有机比例
                    _plat_name = getattr(a, "platform", None) or "unknown"
                    _rnd = a.round_num or 0
                    _bucket = _prc.setdefault(_plat_name, {}).setdefault(
                        _rnd, {"posts": 0, "comments": 0, "likes": 0})
                    _key = classify_organic_action(getattr(a, "action_type", ""))
                    if _key:
                        _bucket[_key] += 1
                _minc = int(getattr(Config, "SIM_ORGANIC_RATIO_MIN_CONSECUTIVE", 3) or 3)
                organic_ratio_warnings = detect_organic_ratio_collapse(_prc, _minc)
        except Exception:  # noqa: BLE001 — 侦测器失败不阻断 run_summary（degrade-safe）
            organic_ratio_warnings = []

        summary = {
            "simulation_id": simulation_id,
            "agent_count": len(agent_stats),
            "total_actions": sum(s["total_actions"] for s in agent_stats),
            "organic_action_count": organic_count,
            "seed_action_count": seed_count,
            "rounds_executed": rounds_executed,
            "simulated_hours": simulated_hours,
            "rounds_with_organic_actions": rounds_with_organic,
            "total_rounds": total_rounds,
            "truncated": truncated,
            "simulation_health": sim_health,
            "peak_round": peak,
            "top_agents": agent_stats[:15],
            "action_volume_by_round": action_volume_by_round,
            "top_posts": top_posts,
        }
        if run_error:
            summary["error"] = str(run_error)[:300]
        if llm_health is not None:
            summary["llm_health"] = llm_health  # RUN-2: 平台级 llm_calls/llm_errors/error_rate
        if agent_dynamics:
            summary["agent_dynamics"] = agent_dynamics  # RUN-9: 报告端可据 active=false 抑制情绪叙事
        if communities:
            summary["communities"] = communities
        if organic_ratio_warnings:
            # ITEM 20: 有机互动塌缩告警——报告端据此对相关平台样本施加「不得叙述为活跃讨论」caveat。
            summary["organic_ratio_warnings"] = organic_ratio_warnings

        try:
            sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
            os.makedirs(sim_dir, exist_ok=True)
            out = os.path.join(sim_dir, "run_summary.json")
            tmp = out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            os.replace(tmp, out)
            logger.info(f"[{simulation_id}] run_summary.json 已写出（{len(agent_stats)} agents, "
                        f"{len(action_volume_by_round)} rounds）")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{simulation_id}] run_summary.json 写出失败（不影响主流程）: {e}")
        return summary

    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        清理模拟的运行日志（用于强制重新开始模拟）
        
        会删除以下文件：
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db（模拟数据库）
        - reddit_simulation.db（模拟数据库）
        - env_status.json（环境状态）
        
        注意：不会删除配置文件（simulation_config.json）和 profile 文件
        
        Args:
            simulation_id: 模拟ID
            
        Returns:
            清理结果信息
        """
        import shutil
        
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "模拟目录不存在，无需清理"}
        
        cleaned_files = []
        errors = []
        
        # 要删除的文件列表（包括数据库文件）
        # RUN-15: 派生产物（run_summary/世界态轨迹/决策流/涌现度量及其 .prev 快照）也在
        # 强制重开时一并删除，防止新一轮失败后报告阶段消费到上一轮的摘要。
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # Twitter 平台数据库
            "reddit_simulation.db",   # Reddit 平台数据库
            "env_status.json",        # 环境状态文件
        ]
        files_to_delete += list(cls._STALE_DERIVED_ARTIFACTS)
        files_to_delete += [f"{name}.prev" for name in cls._STALE_DERIVED_ARTIFACTS]

        # 要删除的目录列表（包含动作日志）
        dirs_to_clean = ["twitter", "reddit"]

        # 删除文件
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"删除 {filename} 失败: {str(e)}")

        # 清理平台目录中的动作日志（含轮转快照与断点续跑检查点，RUN-15/RUN-7）
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                for fname in ("actions.jsonl", "actions.prev.jsonl",
                              "checkpoint.json", "checkpoint.prev.json"):
                    plat_file = os.path.join(dir_path, fname)
                    if os.path.exists(plat_file):
                        try:
                            os.remove(plat_file)
                            cleaned_files.append(f"{dir_name}/{fname}")
                        except Exception as e:
                            errors.append(f"删除 {dir_name}/{fname} 失败: {str(e)}")
        
        # 清理内存中的运行状态
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]
        
        logger.info(f"清理模拟日志完成: {simulation_id}, 删除文件: {cleaned_files}")
        
        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # 防止重复清理的标志
    _cleanup_done = False
    
    @classmethod
    def cleanup_all_simulations(cls):
        """
        清理所有运行中的模拟进程
        
        在服务器关闭时调用，确保所有子进程被终止
        """
        # 防止重复清理
        if cls._cleanup_done:
            return
        cls._cleanup_done = True
        
        # 检查是否有内容需要清理（避免空进程的进程打印无用日志）
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        
        if not has_processes and not has_updaters:
            return  # 没有需要清理的内容，静默返回
        
        logger.info("正在清理所有模拟进程...")
        
        # 首先停止所有图谱记忆更新器（stop_all 内部会打印日志）
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"停止图谱记忆更新器失败: {e}")
        cls._graph_memory_enabled.clear()
        
        # 复制字典以避免在迭代时修改
        processes = list(cls._processes.items())
        
        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # 进程仍在运行
                    logger.info(f"终止模拟进程: {simulation_id}, pid={process.pid}")
                    
                    try:
                        # 使用跨平台的进程终止方法
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # 进程可能已经不存在，尝试直接终止
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()
                    
                    # 更新 run_state.json
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "服务器关闭，模拟被终止"
                        cls._save_run_state(state)
                    
                    # 同时更新 state.json，将状态设为 stopped（原子 + 共享锁，F-6-9）
                    cls._sync_state_json_status(simulation_id, 'stopped')
                    cls._mark_env_stopped(simulation_id)  # RUN-11: 服务器关闭已杀进程，环境同步落 stopped

            except Exception as e:
                logger.error(f"清理进程失败: {simulation_id}, error={e}")
        
        # 清理文件句柄
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()
        
        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()
        
        # 清理内存中的状态
        cls._processes.clear()
        cls._action_queues.clear()
        
        logger.info("模拟进程清理完成")
    
    @classmethod
    def register_cleanup(cls):
        """
        注册清理函数
        
        在 Flask 应用启动时调用，确保服务器关闭时清理所有模拟进程
        """
        global _cleanup_registered
        
        if _cleanup_registered:
            return
        
        # Flask debug 模式下，只在 reloader 子进程中注册清理（实际运行应用的进程）
        # WERKZEUG_RUN_MAIN=true 表示是 reloader 子进程
        # 如果不是 debug 模式，则没有这个环境变量，也需要注册
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        
        # 在 debug 模式下，只在 reloader 子进程中注册；非 debug 模式下始终注册
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # 标记已注册，防止子进程再次尝试
            return
        
        # 保存原有的信号处理器
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP 只在 Unix 系统存在（macOS/Linux），Windows 没有
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)
        
        def cleanup_handler(signum=None, frame=None):
            """信号处理器：先清理模拟进程，再调用原处理器"""
            # 只有在有进程需要清理时才打印日志
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"收到信号 {signum}，开始清理...")
            cls.cleanup_all_simulations()
            
            # 调用原有的信号处理器，让 Flask 正常退出
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: 终端关闭时发送
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # 默认行为：正常退出
                    sys.exit(0)
            else:
                # EXECPLAN2 F-12-3：原处理器为 SIG_DFL / SIG_IGN / 未知。
                # 旧逻辑硬 `raise KeyboardInterrupt`：对 SIGTERM（Docker stop / systemd / supervisor
                # 的默认终止信号）而言，KeyboardInterrupt 是普通 BaseException，会沿被中断帧（Flask/
                # Werkzeug 服务循环）向上传播，可能被某层 try/except 吞掉而无法可靠终止进程。
                # 改为与 PipelineOrchestrator 的同构处理：恢复默认 disposition 并重新投递信号，
                # 让 OS 按默认行为（终止进程）处理；若原本被忽略（SIG_IGN）则保持忽略。
                if signum is not None and signal.getsignal(signum) is signal.SIG_IGN:
                    return
                if signum is not None:
                    signal.signal(signum, signal.SIG_DFL)
                    os.kill(os.getpid(), signum)
                else:
                    # 无信号编号（如直接调用）：退化为正常退出。
                    sys.exit(0)
        
        # 注册 atexit 处理器（作为备用）
        atexit.register(cls.cleanup_all_simulations)
        
        # 注册信号处理器（仅在主线程中）
        try:
            # SIGTERM: kill 命令默认信号
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: 终端关闭（仅 Unix 系统）
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # 不在主线程中，只能使用 atexit
            logger.warning("无法注册信号处理器（不在主线程），仅使用 atexit")
        
        _cleanup_registered = True
    
    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        获取所有正在运行的模拟ID列表
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running
    
    # ============== Interview 功能 ==============
    
    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        检查模拟环境是否存活（可以接收Interview命令）

        Args:
            simulation_id: 模拟ID

        Returns:
            True 表示环境存活，False 表示环境已关闭
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        获取模拟环境的详细状态信息

        Args:
            simulation_id: 模拟ID

        Returns:
            状态详情字典，包含 status, twitter_available, reddit_available, timestamp
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        采访单个Agent

        Args:
            simulation_id: 模拟ID
            agent_id: Agent ID
            prompt: 采访问题
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None: 双平台模拟时同时采访两个平台，返回整合结果
            timeout: 超时时间（秒）

        Returns:
            采访结果字典

        Raises:
            ValueError: 模拟不存在或环境未运行
            TimeoutError: 等待响应超时
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"模拟不存在: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"模拟环境未运行或已关闭，无法执行Interview: {simulation_id}")

        logger.info(f"发送Interview命令: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def _scale_interview_timeout(cls, timeout: float, n_interviews: int) -> float:
        """RUN-12: 让批量采访超时随批量规模与有效并发扩展（只增不减）。

        固定 120/180s 对 80 个 agent × 每平台 4 并发 × 每次 CLI 调用 10-60s 的现实
        必然超时——客户端放弃后模拟端仍在烧额度回答无人消费的问题。公式：
        timeout = max(传入值, 60 + per_agent × ceil(n / 有效并发))。
        INTERVIEW_TIMEOUT_PER_AGENT<=0 时禁用缩放（回到今日行为）。
        """
        try:
            per_agent = float(getattr(Config, "INTERVIEW_TIMEOUT_PER_AGENT", 30.0) or 0.0)
        except (TypeError, ValueError):
            per_agent = 30.0
        if per_agent <= 0 or n_interviews <= 0:
            return timeout
        provider = (os.environ.get('LLM_PROVIDER') or getattr(Config, 'LLM_PROVIDER', '') or 'claude-cli').lower()
        try:
            if provider in ('claude-cli', 'codex-cli'):
                cap = int(os.environ.get('OASIS_CLI_SEMAPHORE', '8') or '8')
            else:
                cap = int(os.environ.get('OASIS_SEMAPHORE', '30') or '30')
        except ValueError:
            cap = 8
        concurrency = max(1, cap // 2)  # 双平台并行时各平台拿一半信号量
        scaled = 60.0 + per_agent * ((n_interviews + concurrency - 1) // concurrency)
        return max(float(timeout), scaled)

    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        批量采访多个Agent

        Args:
            simulation_id: 模拟ID
            interviews: 采访列表，每个元素包含 {"agent_id": int, "prompt": str, "platform": str(可选)}
            platform: 默认平台（可选，会被每个采访项的platform覆盖）
                - "twitter": 默认只采访Twitter平台
                - "reddit": 默认只采访Reddit平台
                - None: 双平台模拟时每个Agent同时采访两个平台
            timeout: 超时时间（秒）

        Returns:
            批量采访结果字典

        Raises:
            ValueError: 模拟不存在或环境未运行
            TimeoutError: 等待响应超时
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"模拟不存在: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"模拟环境未运行或已关闭，无法执行Interview: {simulation_id}")

        # RUN-12: 超时随批量规模缩放（只增不减），避免整批采访必然超时后仍在烧额度。
        scaled_timeout = cls._scale_interview_timeout(timeout, len(interviews))
        if scaled_timeout > timeout:
            logger.info(f"批量Interview超时按规模放大: {timeout}s -> {scaled_timeout}s (n={len(interviews)})")
            timeout = scaled_timeout

        logger.info(f"发送批量Interview命令: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        采访所有Agent（全局采访）

        使用相同的问题采访模拟中的所有Agent

        Args:
            simulation_id: 模拟ID
            prompt: 采访问题（所有Agent使用相同问题）
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None: 双平台模拟时每个Agent同时采访两个平台
            timeout: 超时时间（秒）

        Returns:
            全局采访结果字典
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"模拟不存在: {simulation_id}")

        # 从配置文件获取所有Agent信息
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"模拟配置不存在: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"模拟配置中没有Agent: {simulation_id}")

        # 构建批量采访列表
        interviews = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            if agent_id is not None:
                interviews.append({
                    "agent_id": agent_id,
                    "prompt": prompt
                })

        logger.info(f"发送全局Interview命令: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        关闭模拟环境（而不是停止模拟进程）
        
        向模拟发送关闭环境命令，使其优雅退出等待命令模式
        
        Args:
            simulation_id: 模拟ID
            timeout: 超时时间（秒）
            
        Returns:
            操作结果字典
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        ipc_client = SimulationIPCClient(sim_dir)
        
        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "环境已经关闭"
            }
        
        logger.info(f"发送关闭环境命令: simulation_id={simulation_id}")
        
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            
            return {
                "success": response.status.value == "completed",
                "message": "环境关闭命令已发送",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # 超时可能是因为环境正在关闭
            return {
                "success": True,
                "message": "环境关闭命令已发送（等待响应超时，环境可能正在关闭）"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """从单个数据库获取Interview历史"""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"读取Interview历史失败 ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取Interview历史记录（从数据库读取）
        
        Args:
            simulation_id: 模拟ID
            platform: 平台类型（reddit/twitter/None）
                - "reddit": 只获取Reddit平台的历史
                - "twitter": 只获取Twitter平台的历史
                - None: 获取两个平台的所有历史
            agent_id: 指定Agent ID（可选，只获取该Agent的历史）
            limit: 每个平台返回数量限制
            
        Returns:
            Interview历史记录列表
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        results = []
        
        # 确定要查询的平台
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # 不指定platform时，查询两个平台
            platforms = ["twitter", "reddit"]
        
        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)
        
        # 按时间降序排序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # 如果查询了多个平台，限制总数
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]
        
        return results

