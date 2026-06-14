"""
模拟IPC通信模块
用于Flask后端和模拟脚本之间的进程间通信

通过文件系统实现简单的命令/响应模式：
1. Flask写入命令到 commands/ 目录
2. 模拟脚本轮询命令目录，执行命令并写入响应到 responses/ 目录
3. Flask轮询响应目录获取结果
"""

import os
import json
import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..utils.logger import get_logger
from ..utils.atomic import write_json_atomic

logger = get_logger('mirofish.simulation_ipc')

# 已被客户端放弃的命令所留下的取消标记 / 服务端认领标记后缀。
# EXECPLAN F-12-7: 用 .cancel 取消信号代替直接删除命令文件，用 .processing 认领命令。
_CANCEL_SUFFIX = ".cancel"
_PROCESSING_SUFFIX = ".processing"

# EXECPLAN2 I-5-5: 每条 IPC 命令往返的轻量遥测落地文件名（位于 simulation_dir 下）。
# 采访（interview/batch_interview）的延迟分布与超时率直接解释报告丰富度——
# 报告读起来单薄到底是「模型选择不采访」还是「采访一直超时」可由此区分。
_IPC_TELEMETRY_FILENAME = "ipc_telemetry.jsonl"


def _ipc_telemetry_enabled() -> bool:
    """是否开启 IPC 往返遥测（默认关，保持现有行为）。EXECPLAN2 I-5-5

    本文件不拥有 config.py，故通过 getattr 读取 Config.IPC_TELEMETRY_ENABLED，
    未配置时回落为 False；导入失败也安全降级为关闭，绝不影响采访语义。
    """
    try:
        from ..config import Config
        return bool(getattr(Config, "IPC_TELEMETRY_ENABLED", False))
    except Exception:
        return False


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    """尽力而为地向 JSONL 文件追加一行（失败静默）。EXECPLAN2 I-5-5

    遥测是纯旁路测量：任何 I/O 失败都不得影响命令收发的成功/失败语义或时序，
    因此整段包在 try/except 中，并仅以追加方式写入避免覆盖既有记录。
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except (OSError, TypeError, ValueError):
        # 遥测失败绝不冒泡到调用方；丢一行测量数据可以接受。
        pass


def _percentile(sorted_values: List[float], pct: float) -> float:
    """对已升序排序的列表取近似分位数（最近秩法）。EXECPLAN2 I-5-5

    用于汇总 p50/p95 延迟。空列表返回 0.0；pct 取 [0,100]。
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pct = max(0.0, min(100.0, pct))
    # 最近秩：rank = ceil(pct/100 * N)，转 0 基索引并夹取边界。
    import math
    rank = int(math.ceil((pct / 100.0) * len(sorted_values)))
    idx = min(max(rank - 1, 0), len(sorted_values) - 1)
    return float(sorted_values[idx])

# EXECPLAN F-6-6 / F-12-7: 陈旧 IPC 文件的最小回收阈值（秒）。
# 命令/响应文件名都是一次性 UUID，超过阈值的文件必然是无人认领的孤儿，
# 直接清理不会误删任何还在等待的活跃消费者。
_MIN_STALE_SWEEP_SECONDS = 300.0


def _sweep_stale_files(directory: str, max_age_seconds: float) -> None:
    """清理目录下 mtime 超过 max_age_seconds 的 IPC 临时文件（尽力而为）。

    EXECPLAN F-6-6 / F-12-7: 锚定在 mtime 而非已被删除的命令文件，
    保证孤儿响应、被取消的命令、遗留的 .processing 标记都能被回收，
    使 ipc_commands/ipc_responses 目录不会随会话无限增长。
    """
    if not os.path.isdir(directory):
        return
    now = time.time()
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for filename in entries:
        if not (
            filename.endswith('.json')
            or filename.endswith(_CANCEL_SUFFIX)
            or filename.endswith(_PROCESSING_SUFFIX)
        ):
            continue
        filepath = os.path.join(directory, filename)
        try:
            if now - os.path.getmtime(filepath) > max_age_seconds:
                os.remove(filepath)
        except OSError:
            # 文件可能被服务端并发清理；忽略即可。
            pass


class CommandType(str, Enum):
    """命令类型"""
    INTERVIEW = "interview"           # 单个Agent采访
    BATCH_INTERVIEW = "batch_interview"  # 批量采访
    CLOSE_ENV = "close_env"           # 关闭环境


class CommandStatus(str, Enum):
    """命令状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IPCCommand:
    """IPC命令"""
    command_id: str
    command_type: CommandType
    args: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "args": self.args,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCCommand':
        return cls(
            command_id=data["command_id"],
            command_type=CommandType(data["command_type"]),
            args=data.get("args", {}),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


@dataclass
class IPCResponse:
    """IPC响应"""
    command_id: str
    status: CommandStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCResponse':
        return cls(
            command_id=data["command_id"],
            status=CommandStatus(data["status"]),
            result=data.get("result"),
            error=data.get("error"),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


class SimulationIPCClient:
    """
    模拟IPC客户端（Flask端使用）
    
    用于向模拟进程发送命令并等待响应
    """
    
    def __init__(self, simulation_dir: str):
        """
        初始化IPC客户端
        
        Args:
            simulation_dir: 模拟数据目录
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")
        
        # 确保目录存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def send_command(
        self,
        command_type: CommandType,
        args: Dict[str, Any],
        timeout: float = 60.0,
        poll_interval: float = 0.5
    ) -> IPCResponse:
        """
        发送命令并等待响应
        
        Args:
            command_type: 命令类型
            args: 命令参数
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
            
        Returns:
            IPCResponse
            
        Raises:
            TimeoutError: 等待响应超时
        """
        # EXECPLAN F-6-6 / F-12-7: 在发起新命令前清理陈旧的孤儿文件。
        # 超过 max(timeout*2, 300s) 的命令/响应/取消标记必为已放弃的请求，
        # 借此为服务端守卫的 TOCTOU 竞态窗口兜底，限制目录无限增长。
        stale_age = max(timeout * 2.0, _MIN_STALE_SWEEP_SECONDS)
        _sweep_stale_files(self.responses_dir, stale_age)
        _sweep_stale_files(self.commands_dir, stale_age)

        command_id = str(uuid.uuid4())
        command = IPCCommand(
            command_id=command_id,
            command_type=command_type,
            args=args
        )

        # EXECPLAN2 I-5-5: 轻量遥测——记录往返延迟、轮询次数、超时率，并归因到
        # agent_id / platform。仅在 Config.IPC_TELEMETRY_ENABLED 开启时落盘；
        # 整条测量路径 best-effort，绝不影响采访收发语义或时序。
        telemetry_on = _ipc_telemetry_enabled()
        agent_id = args.get("agent_id") if isinstance(args, dict) else None
        platform = args.get("platform") if isinstance(args, dict) else None
        poll_count = 0

        # 写入命令文件（原子写，避免服务端轮询读到半截 JSON）。EXECPLAN F-6-6
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        write_json_atomic(command_file, command.to_dict())

        logger.info(f"发送IPC命令: {command_type.value}, command_id={command_id}")

        # 等待响应
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if os.path.exists(response_file):
                try:
                    with open(response_file, 'r', encoding='utf-8') as f:
                        response_data = json.load(f)
                    response = IPCResponse.from_dict(response_data)

                    # 清理命令和响应文件（含服务端认领标记与可能遗留的取消标记）。
                    # EXECPLAN F-12-7: 命令文件可能已被服务端 rename 成 .processing，
                    # 逐个尽力而为删除，互不影响。
                    for path in (
                        command_file,
                        os.path.join(self.commands_dir, f"{command_id}{_PROCESSING_SUFFIX}"),
                        os.path.join(self.commands_dir, f"{command_id}{_CANCEL_SUFFIX}"),
                        response_file,
                    ):
                        try:
                            os.remove(path)
                        except OSError:
                            pass

                    logger.info(f"收到IPC响应: command_id={command_id}, status={response.status.value}")
                    # EXECPLAN2 I-5-5: 记录已完成往返（completed/failed 取决于服务端状态）。
                    if telemetry_on:
                        self._record_ipc_telemetry(
                            command_id=command_id,
                            command_type=command_type,
                            agent_id=agent_id,
                            platform=platform,
                            latency_ms=(time.time() - start_time) * 1000.0,
                            status=(
                                "completed"
                                if response.status == CommandStatus.COMPLETED
                                else "failed"
                            ),
                            poll_count=poll_count,
                            timeout=timeout,
                        )
                    return response
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"解析响应失败: {e}")

            poll_count += 1  # EXECPLAN2 I-5-5: 统计轮询次数，反映等待深度。
            time.sleep(poll_interval)

        # 超时
        logger.error(f"等待IPC响应超时: command_id={command_id}")
        # EXECPLAN2 I-5-5: 记录超时往返——这是「报告单薄是因为采访一直超时」的关键信号。
        if telemetry_on:
            self._record_ipc_telemetry(
                command_id=command_id,
                command_type=command_type,
                agent_id=agent_id,
                platform=platform,
                latency_ms=(time.time() - start_time) * 1000.0,
                status="timeout",
                poll_count=poll_count,
                timeout=timeout,
            )

        # EXECPLAN F-12-7: 写入 .cancel 取消标记，让服务端在执行前/写响应前
        # 感知到客户端已放弃，从而跳过 LLM 采访或避免写出无人消费的孤儿响应。
        cancel_file = os.path.join(self.commands_dir, f"{command_id}{_CANCEL_SUFFIX}")
        try:
            write_json_atomic(cancel_file, {
                "command_id": command_id,
                "cancelled_at": datetime.now().isoformat()
            })
        except OSError:
            pass

        # 仍保留尽力而为地删除命令文件（仅作为兜底；若服务端已认领为
        # .processing 则此处删不到，由 .cancel 标记与陈旧扫描兜底回收）。
        try:
            os.remove(command_file)
        except OSError:
            pass

        raise TimeoutError(f"等待命令响应超时 ({timeout}秒)")
    
    def send_interview(
        self,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> IPCResponse:
        """
        发送单个Agent采访命令
        
        Args:
            agent_id: Agent ID
            prompt: 采访问题
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台  
                - None: 双平台模拟时同时采访两个平台，单平台模拟时采访该平台
            timeout: 超时时间
            
        Returns:
            IPCResponse，result字段包含采访结果
        """
        args = {
            "agent_id": agent_id,
            "prompt": prompt
        }
        if platform:
            args["platform"] = platform
            
        return self.send_command(
            command_type=CommandType.INTERVIEW,
            args=args,
            timeout=timeout
        )
    
    def send_batch_interview(
        self,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> IPCResponse:
        """
        发送批量采访命令
        
        Args:
            interviews: 采访列表，每个元素包含 {"agent_id": int, "prompt": str, "platform": str(可选)}
            platform: 默认平台（可选，会被每个采访项的platform覆盖）
                - "twitter": 默认只采访Twitter平台
                - "reddit": 默认只采访Reddit平台
                - None: 双平台模拟时每个Agent同时采访两个平台
            timeout: 超时时间
            
        Returns:
            IPCResponse，result字段包含所有采访结果
        """
        args = {"interviews": interviews}
        if platform:
            args["platform"] = platform
            
        return self.send_command(
            command_type=CommandType.BATCH_INTERVIEW,
            args=args,
            timeout=timeout
        )
    
    def send_close_env(self, timeout: float = 30.0) -> IPCResponse:
        """
        发送关闭环境命令
        
        Args:
            timeout: 超时时间
            
        Returns:
            IPCResponse
        """
        return self.send_command(
            command_type=CommandType.CLOSE_ENV,
            args={},
            timeout=timeout
        )
    
    def check_env_alive(self) -> bool:
        """
        检查模拟环境是否存活
        
        通过检查 env_status.json 文件来判断
        """
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        if not os.path.exists(status_file):
            return False
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return status.get("status") == "alive"
        except (json.JSONDecodeError, OSError):
            return False

    def _record_ipc_telemetry(
        self,
        *,
        command_id: str,
        command_type: CommandType,
        agent_id: Optional[int],
        platform: Optional[str],
        latency_ms: float,
        status: str,
        poll_count: int,
        timeout: float,
    ) -> None:
        """向 simulation_dir/ipc_telemetry.jsonl 追加一条往返测量。EXECPLAN2 I-5-5

        每行 {ts, command_id, command_type, agent_id, platform, latency_ms,
        status, poll_count, timeout_s}。纯旁路、best-effort，绝不抛出。
        采访超时会静默饿死报告里的 agent 视角；这条记录把该退化变得可诊断。
        """
        record = {
            "ts": datetime.now().isoformat(),
            "command_id": command_id,
            "command_type": command_type.value,
            "agent_id": agent_id,
            "platform": platform,
            "latency_ms": round(float(latency_ms), 1),
            "status": status,
            "poll_count": int(poll_count),
            "timeout_s": float(timeout),
        }
        _append_jsonl(
            os.path.join(self.simulation_dir, _IPC_TELEMETRY_FILENAME), record
        )

    @classmethod
    def summarize(cls, simulation_dir: str) -> Dict[str, Any]:
        """汇总 simulation_dir 下的 IPC 往返遥测。EXECPLAN2 I-5-5

        读取 ipc_telemetry.jsonl，返回总体与按命令类型的健康度统计：
        {count, completed, failed, timeout, timeout_rate, p50_ms, p95_ms,
         by_command: {<command_type>: {count, timeout_rate, p50_ms, p95_ms}}}。

        让报告阶段/运维可以一眼看出采访是否健康（延迟分布 + 超时率），
        从而把「报告单薄」归因到模型选择还是基础设施退化，并据 p95 调超时。
        遥测缺失/解析失败时返回零值骨架，绝不抛出。
        """
        empty = {
            "count": 0,
            "completed": 0,
            "failed": 0,
            "timeout": 0,
            "timeout_rate": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "by_command": {},
        }
        path = os.path.join(simulation_dir, _IPC_TELEMETRY_FILENAME)
        if not os.path.isfile(path):
            return empty

        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        # 容忍写一半的尾行/损坏行，跳过即可。
                        continue
        except OSError:
            return empty

        if not records:
            return empty

        def _bucket(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            count = len(rows)
            completed = sum(1 for r in rows if r.get("status") == "completed")
            failed = sum(1 for r in rows if r.get("status") == "failed")
            timeout = sum(1 for r in rows if r.get("status") == "timeout")
            latencies = sorted(
                float(r.get("latency_ms", 0.0) or 0.0) for r in rows
            )
            return {
                "count": count,
                "completed": completed,
                "failed": failed,
                "timeout": timeout,
                "timeout_rate": round(timeout / count, 4) if count else 0.0,
                "p50_ms": round(_percentile(latencies, 50.0), 1),
                "p95_ms": round(_percentile(latencies, 95.0), 1),
            }

        summary = _bucket(records)
        by_command: Dict[str, Any] = {}
        for rec in records:
            ct = str(rec.get("command_type", "unknown"))
            by_command.setdefault(ct, []).append(rec)
        summary["by_command"] = {
            ct: _bucket(rows) for ct, rows in by_command.items()
        }
        return summary


class SimulationIPCServer:
    """
    模拟IPC服务器（模拟脚本端使用）
    
    轮询命令目录，执行命令并返回响应
    """
    
    def __init__(self, simulation_dir: str):
        """
        初始化IPC服务器
        
        Args:
            simulation_dir: 模拟数据目录
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")
        
        # 确保目录存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

        # 环境状态
        self._running = False

    def _sweep_stale(self):
        """启动/停止时回收陈旧的命令、响应及标记文件。EXECPLAN F-12-7 #3"""
        _sweep_stale_files(self.commands_dir, _MIN_STALE_SWEEP_SECONDS)
        _sweep_stale_files(self.responses_dir, _MIN_STALE_SWEEP_SECONDS)

    def start(self):
        """标记服务器为运行状态"""
        self._running = True
        # EXECPLAN F-12-7: 启动时清理上次会话遗留的孤儿文件，避免跨会话累积。
        self._sweep_stale()
        self._update_env_status("alive")

    def stop(self):
        """标记服务器为停止状态"""
        self._running = False
        # EXECPLAN F-12-7: 停止时再清理一次，避免被取消/超时的请求文件长期遗留。
        self._sweep_stale()
        self._update_env_status("stopped")
    
    def _update_env_status(self, status: str):
        """更新环境状态文件（原子写，避免客户端轮询读到半截 JSON）。EXECPLAN F-6-6"""
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        write_json_atomic(status_file, {
            "status": status,
            "timestamp": datetime.now().isoformat()
        })
    
    def poll_commands(self) -> Optional[IPCCommand]:
        """
        轮询命令目录，返回第一个待处理的命令

        EXECPLAN F-12-7: 在读取/执行前先原子地把命令文件 rename 成
        <command_id>.json.processing 完成“认领”，消除“读取-执行-删除”之间的
        TOCTOU 竞态，并避免命令被重复挑选/执行。已被客户端写入 .cancel
        取消标记的命令直接跳过并清理，不再浪费 LLM 调用。

        Returns:
            IPCCommand 或 None
        """
        if not os.path.exists(self.commands_dir):
            return None

        # 按时间排序获取尚未认领的命令文件（忽略 .processing / .cancel 标记）。
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(self.commands_dir, filename)
            try:
                command_files.append((filepath, filename, os.path.getmtime(filepath)))
            except OSError:
                continue

        command_files.sort(key=lambda x: x[2])

        for filepath, filename, _ in command_files:
            command_id = filename[:-len('.json')]

            # EXECPLAN F-12-7: 客户端已取消，跳过并清理命令与取消标记。
            cancel_file = os.path.join(self.commands_dir, f"{command_id}{_CANCEL_SUFFIX}")
            if os.path.exists(cancel_file):
                for path in (filepath, cancel_file):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                continue

            # EXECPLAN F-12-7: 原子认领——rename 成功才算抢到该命令；
            # 失败说明被并发认领或客户端取消/超时删除，跳过即可。
            processing_file = os.path.join(
                self.commands_dir, f"{command_id}{_PROCESSING_SUFFIX}"
            )
            try:
                os.rename(filepath, processing_file)
            except OSError:
                continue

            # 认领后再次检查取消标记，缩小客户端在 rename 期间取消的窗口。
            if os.path.exists(cancel_file):
                for path in (processing_file, cancel_file):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                continue

            try:
                with open(processing_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return IPCCommand.from_dict(data)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"读取命令文件失败: {processing_file}, {e}")
                # 损坏的认领文件直接清理，避免反复读取失败后残留。
                try:
                    os.remove(processing_file)
                except OSError:
                    pass
                continue

        return None
    
    def send_response(self, response: IPCResponse):
        """
        发送响应

        EXECPLAN F-6-6: 客户端在超时时会删除命令文件并写入 .cancel 取消标记。
        若发现客户端已放弃（认领/命令文件都不在，或存在取消标记），则跳过写出
        响应，避免在 ipc_responses/ 留下无人消费的孤儿文件造成磁盘缓慢泄漏。

        Args:
            response: IPC响应
        """
        command_id = response.command_id
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        processing_file = os.path.join(
            self.commands_dir, f"{command_id}{_PROCESSING_SUFFIX}"
        )
        cancel_file = os.path.join(self.commands_dir, f"{command_id}{_CANCEL_SUFFIX}")

        client_cancelled = os.path.exists(cancel_file)
        client_gone = not (os.path.exists(command_file) or os.path.exists(processing_file))

        if client_cancelled or client_gone:
            # EXECPLAN F-6-6: 客户端已超时/取消，不写孤儿响应，仅清理标记文件。
            for path in (command_file, processing_file, cancel_file):
                try:
                    os.remove(path)
                except OSError:
                    pass
            logger.info(
                f"客户端已放弃命令，跳过写出响应: command_id={command_id}"
            )
            return

        # 原子写出响应，避免客户端轮询读到半截 JSON。EXECPLAN F-6-6
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        write_json_atomic(response_file, response.to_dict())

        # 删除命令文件及认领/取消标记（尽力而为）。
        for path in (command_file, processing_file, cancel_file):
            try:
                os.remove(path)
            except OSError:
                pass
    
    def send_success(self, command_id: str, result: Dict[str, Any]):
        """发送成功响应"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.COMPLETED,
            result=result
        ))
    
    def send_error(self, command_id: str, error: str):
        """发送错误响应"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.FAILED,
            error=error
        ))
