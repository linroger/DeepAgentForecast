"""统一管线编排器 (PipelineOrchestrator)

把 DeerFlow 深度研究 (Step 0) 接到 MiroFish 现有的五步预测管线之前，实现
"一个 prompt 进，预测报告出"：

    prompt
      → [research]  DeerFlow 子进程深度调研 → handoff/ (research_report.md, actors.json)
      → [ontology]  用研究报告做种子文本 + prompt 做预测需求 → 本体
      → [graph]     构建 Zep 知识图谱
      → [prepare]   生成 persona + 模拟配置
      → [run]       OASIS 双平台模拟
      → [report]    ReportAgent 生成预测报告

设计要点
--------
* DeerFlow 运行在它自己的 venv（依赖树与 MiroFish 隔离），通过 subprocess 调用
  仓库内 deer-flow/ 的 ``deerflow_research.py``，消费其写出的文件化 handoff 契约。
  这一模式与 ``SimulationRunner`` 驱动 OASIS 进程完全一致。
* 编排器在后台 daemon 线程中运行，进度同时写入：
    - 全局 ``TaskManager`` 任务（沿用 MiroFish 既有的轮询机制）；
    - ``uploads/pipelines/<id>/pipeline_state.json``（断点续看 + 各阶段细分进度）。
* 复用现有 service，不走 HTTP；阶段间通过 project_id → graph_id → simulation_id
  → report_id 串联。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..config import Config
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager
from ..services.graph_builder import GraphBuilderService
from ..services.ontology_generator import OntologyGenerator
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import RunnerStatus, SimulationRunner
from ..services.text_processor import TextProcessor
from ..services.zep_graph_memory_updater import ZepGraphMemoryManager
from ..utils.actors import (
    REL_LABEL,
    extract_relationship_rows,
    situation_brief,
    situation_brief_block,
)
from ..utils.dates import parse_as_of
from ..utils.logger import get_logger

logger = get_logger('mirofish.pipeline')


# ---------------------------------------------------------------------------
# 阶段定义与全局进度权重（每个阶段在全局 0-100 中占的区间）
# ---------------------------------------------------------------------------

STAGE_RESEARCH = "research"
STAGE_ONTOLOGY = "ontology"
STAGE_GRAPH = "graph"
STAGE_PREPARE = "prepare"
STAGE_RUN = "run"
STAGE_REPORT = "report"

# (起点, 终点) 全局百分比区间
STAGE_BANDS: dict[str, tuple[int, int]] = {
    STAGE_RESEARCH: (0, 30),
    STAGE_ONTOLOGY: (30, 40),
    STAGE_GRAPH: (40, 60),
    STAGE_PREPARE: (60, 72),
    STAGE_RUN: (72, 92),
    STAGE_REPORT: (92, 100),
}

# research_only 模式下，研究阶段独占 0-100
RESEARCH_ONLY_BANDS: dict[str, tuple[int, int]] = {STAGE_RESEARCH: (0, 100)}

# I-4-4: pipeline_state.json 的 schema 版本。每次形状演进 +1，并在 _MIGRATIONS 注册
# 一个把「上一版 → 本版」就地补齐的纯函数。v1 = 历史无 schema_version 字段的状态文件。
# v2 = 引入显式 schema_version + run.json manifest 指针（artifacts['run_manifest']）。
PIPELINE_SCHEMA_VERSION = 2


class PipelineCancelled(BaseException):
    """用户主动取消管线（与失败区分开：不是错误，是决定）。

    继承 BaseException 而非 Exception：管线各阶段（章节级容错、模拟准备等）布有
    大量 ``except Exception`` 的纵深防御层，取消信号必须穿透它们直达 ``_run`` 的
    取消处理器——否则取消会被降级成"占位符章节"或"阶段失败"，状态被误标。
    （与 KeyboardInterrupt 同类的控制流信号，不是可恢复错误。）
    """


class IncompatiblePipelineSchema(RuntimeError):
    """I-4-4: 试图操作一个由更新版代码写出的 pipeline_state.json。

    严格模式（Config.PIPELINE_STRICT_SCHEMA，默认 True）下，对 resume/continue/fork
    等会重写状态文件的操作抛出，避免旧二进制按旧语义解析丢字段、再落盘降级覆写。
    API 层应把它映射成 409 Conflict（而非 500），并提示「升级后端或开启
    PIPELINE_STRICT_SCHEMA=false 应急旁路」。
    """

    def __init__(self, pipeline_id: str, file_version: int):
        self.pipeline_id = pipeline_id
        self.file_version = file_version
        super().__init__(
            f"管线 {pipeline_id} 的状态文件 schema 版本为 {file_version}，"
            f"高于当前后端支持的 {PIPELINE_SCHEMA_VERSION}。"
            "请升级后端，或在 .env 设 PIPELINE_STRICT_SCHEMA=false 应急旁路（可能丢失新字段）。"
        )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# I-4-1: 进程级 boot id（每次后端进程启动唯一）。配合持久化的 owner_pid，让
# reconcile_orphans 能区分「本进程拥有的在飞管线」与「上一进程/别的 worker 遗留的孤儿」，
# 而不再仅凭 in-process _threads 判定（多 worker / 重启后 _threads 会丢失真相）。
_BOOT_ID = uuid.uuid4().hex


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """I-4-1/I-5-6: 解析 _utcnow() 写出的 ISO 时间戳；解析失败返回 None（best-effort）。"""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    # 旧文件可能写的是 naive 时间戳；统一补成 UTC，使与 _utcnow() 的 aware 比较安全。
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_seconds(ts: Optional[str]) -> Optional[float]:
    """I-4-1/I-5-6: 自 ts 起经过的秒数（now-ts）；ts 不可解析返回 None。"""
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _pid_alive(pid: Optional[int]) -> bool:
    """I-4-1: 进程是否存活（os.kill(pid, 0) 探活，不依赖第三方 psutil）。

    pid 无效 / 已退出 → False；权限不足（EPERM）说明进程确实存在但非本用户 → 视为存活，
    保守地避免把别人的存活进程误判为死。
    """
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


@dataclass
class StageState:
    name: str
    status: str = "pending"          # pending / running / completed / failed / skipped
    progress: int = 0                # 0-100 (阶段内)
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageState":
        return cls(
            name=data.get("name", ""),
            status=data.get("status", "pending"),
            progress=int(data.get("progress") or 0),
            message=data.get("message", ""),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
        )


@dataclass
class PipelineState:
    pipeline_id: str
    prompt: str
    # I-4-4: 状态文件 schema 版本（每次落盘写当前版本；load 时按需向前迁移）。
    schema_version: int = PIPELINE_SCHEMA_VERSION
    mode: str = "full"               # full / research_only
    status: str = "pending"          # pending / running / completed / failed
    global_progress: int = 0
    current_stage: str = ""
    task_id: Optional[str] = None
    # 各阶段产物 id
    project_id: Optional[str] = None
    graph_id: Optional[str] = None
    simulation_id: Optional[str] = None
    report_id: Optional[str] = None
    handoff_dir: Optional[str] = None
    # 在飞研究子进程的 PID（进程组长）。持久化后，后端崩溃重启时
    # reconcile_orphans 能找到并杀掉仍在烧额度的孤儿研究进程。
    research_pid: Optional[int] = None
    # I-4-1: 拥有该在飞管线的后端进程指纹（owner_pid + owner_boot_id）与按固定壁钟节律刷新的
    # 心跳时间戳。reconcile_orphans 据此把「死管线」与「慢但活的管线」区分开（owner 进程不在/
    # boot_id 不符 且 心跳过期 才回收）。老状态文件缺失 → None，回退到旧的 _threads 判定。
    owner_pid: Optional[int] = None
    owner_boot_id: Optional[str] = None
    heartbeat_at: Optional[str] = None
    # I-5-6: 最近一次进度回调的壁钟时间戳。供状态 API 计算 stale（now-last_progress_at>阈值
    # 即「还在思考」而非「卡死」）。每次 _make_stage_updater.update() 刷新。
    last_progress_at: Optional[str] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    options: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, StageState] = field(default_factory=dict)
    # T6.3: 各阶段产物的可深链指针（stage 名 → handoff 相对文件名），随阶段完成填充。
    # 供 GET /api/research/<id>/artifact/<name> + StageTimeline 的「view →」入口。
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineState":
        stages = {
            name: StageState.from_dict(stage if isinstance(stage, dict) else {"name": name})
            for name, stage in (data.get("stages") or {}).items()
        }
        return cls(
            pipeline_id=data["pipeline_id"],
            prompt=data.get("prompt", ""),
            # I-4-4: 缺失则视为 v1（历史文件）。load() 已在更上游做过迁移，这里只是兜底。
            schema_version=int(data.get("schema_version") or 1),
            mode=data.get("mode", "full"),
            status=data.get("status", "pending"),
            global_progress=int(data.get("global_progress") or 0),
            current_stage=data.get("current_stage", ""),
            task_id=data.get("task_id"),
            project_id=data.get("project_id"),
            graph_id=data.get("graph_id"),
            simulation_id=data.get("simulation_id"),
            report_id=data.get("report_id"),
            handoff_dir=data.get("handoff_dir"),
            research_pid=data.get("research_pid"),
            # I-4-1 / I-5-6: 老状态文件无这些字段 → None（回退旧 _threads 判定 / 无 stale 信息）。
            owner_pid=data.get("owner_pid"),
            owner_boot_id=data.get("owner_boot_id"),
            heartbeat_at=data.get("heartbeat_at"),
            last_progress_at=data.get("last_progress_at"),
            error=data.get("error"),
            created_at=data.get("created_at") or _utcnow(),
            updated_at=data.get("updated_at") or _utcnow(),
            options=data.get("options") or {},
            stages=stages,
            artifacts=data.get("artifacts") or {},  # T6.3: 老状态文件缺失则默认空 dict
        )


# ---------------------------------------------------------------------------
# 管线状态持久化（file-backed，沿用 MiroFish 的目录约定）
# ---------------------------------------------------------------------------


class PipelineManager:
    """读写 uploads/pipelines/<id>/pipeline_state.json。"""

    # 管线 id 形如 pipe_<hex>（见 create()/fork()），亦含少量历史/手工 id（如 pipe_e2egold02）。
    # 允许字母数字/下划线/连字符；不含 '.' '/' '\\' 故天然无法 ..  逃逸（EXECPLAN2 F-13-4）。
    _PIPELINE_ID_RE = re.compile(r"^pipe_[A-Za-z0-9_-]+$")

    @classmethod
    def _validate_id(cls, pipeline_id: str) -> str:
        if (not pipeline_id or "/" in pipeline_id or "\\" in pipeline_id
                or ".." in pipeline_id or not cls._PIPELINE_ID_RE.match(pipeline_id)):
            raise ValueError(f"invalid pipeline_id: {pipeline_id!r}")
        return pipeline_id

    @classmethod
    def _dir(cls, pipeline_id: str) -> str:
        cls._validate_id(pipeline_id)
        return os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id)

    @classmethod
    def state_path(cls, pipeline_id: str) -> str:
        return os.path.join(cls._dir(pipeline_id), "pipeline_state.json")

    @classmethod
    def handoff_dir(cls, pipeline_id: str) -> str:
        return os.path.join(cls._dir(pipeline_id), "handoff")

    @classmethod
    def manifest_path(cls, pipeline_id: str) -> str:
        """I-8-1: uploads/pipelines/<id>/run.json 的路径（可复现性清单）。"""
        return os.path.join(cls._dir(pipeline_id), "run.json")

    @classmethod
    def ensure_dirs(cls, pipeline_id: str) -> None:
        os.makedirs(cls.handoff_dir(pipeline_id), exist_ok=True)

    # I-4-4: 当 load() 读到一个 schema_version 比当前运行代码更新的状态文件时返回的哨兵。
    # 旧二进制不知道新版的语义，贸然按旧 from_dict 解析可能丢字段并在下次落盘时把新文件
    # 「降级覆写」损坏。哨兵让上层（API）把它映射成 409 Conflict 而不是 500。
    INCOMPATIBLE_KEY = "__incompatible_schema__"

    # ORCH-5: 每管线一把进程内写锁。唯一 tmp 名只消除了 FileNotFoundError 竞态；
    # touch_heartbeat/mark_failed 的「load→改→整 dict 写回」与主线程 save() 交错时仍有
    # lost-update：心跳线程读到旧 state 后原子覆写，静默回滚主线程刚落盘的阶段进度/产物，
    # 最坏把 terminal 状态翻回 running（下次重启即被孤儿回收误杀）。全部写方都在本进程
    # （管线线程/心跳线程/API 线程），进程内锁足以串行化读改写窗口，文件格式不变。
    _state_locks: "dict[str, threading.Lock]" = {}
    _state_locks_guard = threading.Lock()

    @classmethod
    def _state_lock(cls, pipeline_id: str) -> threading.Lock:
        with cls._state_locks_guard:
            lk = cls._state_locks.get(pipeline_id)
            if lk is None:
                lk = cls._state_locks[pipeline_id] = threading.Lock()
            return lk

    @classmethod
    def save(cls, state: PipelineState) -> None:
        cls.ensure_dirs(state.pipeline_id)
        state.updated_at = _utcnow()
        # I-4-4: 每次落盘都写当前 schema 版本（即便 dataclass 实例由旧文件迁移而来）。
        state.schema_version = PIPELINE_SCHEMA_VERSION
        # 走 write_json_atomic（tempfile.mkstemp 生成「每次唯一」的 tmp 名）。此前三处 state 写入
        # 共用同一个硬编码 `pipeline_state.json.tmp`，心跳线程(touch_heartbeat)与主线程 save() 并发时
        # 会竞态：一方 os.replace 把共享 tmp 改走后，另一方 os.replace 找不到 tmp → FileNotFoundError，
        # 研究阶段（双轨+心跳长跑）必现。唯一 tmp 名彻底消除该竞态。
        from ..utils.atomic import write_json_atomic
        with cls._state_lock(state.pipeline_id):  # ORCH-5: 与心跳/终态直写串行化
            write_json_atomic(cls.state_path(state.pipeline_id), state.to_dict())

    # I-4-4: 有序迁移函数，键 = 「源版本」，把 vN 的 dict 就地补齐到 v(N+1)。
    # 必须纯且幂等（在 load 时反复运行也安全）。新增一版时：PIPELINE_SCHEMA_VERSION += 1，
    # 并在此注册 {上一版: _migrate_to_next}。
    @staticmethod
    def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
        """v1（无 schema_version 的历史文件）→ v2。

        历史文件不会有 artifacts['run_manifest'] / run.json，缺失即按无 manifest 降级；
        这里只确保 artifacts 是 dict（极老的文件可能整体缺该键），其余字段由 from_dict 兜底。
        """
        if not isinstance(data.get("artifacts"), dict):
            data["artifacts"] = {}
        return data

    @classmethod
    def _migrations(cls) -> "dict[int, Callable[[dict[str, Any]], dict[str, Any]]]":
        """源版本 → 迁移函数。集中一处，新增版本只改这里 + PIPELINE_SCHEMA_VERSION。"""
        return {1: cls._migrate_v1_to_v2}

    @classmethod
    def _migrate(cls, data: dict[str, Any], from_version: int) -> dict[str, Any]:
        """把 data 从 from_version 依次迁移到 PIPELINE_SCHEMA_VERSION（就地 + 返回）。"""
        migrations = cls._migrations()
        ver = from_version
        while ver < PIPELINE_SCHEMA_VERSION:
            fn = migrations.get(ver)
            if fn is None:
                # 没有登记的迁移函数：保守跳过（不修改），仅推进版本号，避免死循环。
                break
            data = fn(data)
            ver += 1
        data["schema_version"] = PIPELINE_SCHEMA_VERSION
        return data

    @classmethod
    def mark_failed(cls, pipeline_id: str, error: str, status: str = "failed") -> bool:
        """直接在持久化 JSON 上把管线标记为终态（无需重建 dataclass）。

        用于启动时回收孤儿管线：进程崩溃/重启后，pipeline_state.json 可能永远停在
        running，前端轮询据此空转。原子写入（tmp + os.replace），同时把当前阶段标为
        同一终态。``status`` 允许 "cancelled"（用户对孤儿管线点取消时语义更准确）。
        """
        with cls._state_lock(pipeline_id):  # ORCH-5: 读改写全程持锁
            data = cls.load(pipeline_id)
            if not data:
                return False
            data["status"] = status
            data["error"] = error
            data["updated_at"] = _utcnow()
            cur = data.get("current_stage")
            stages = data.get("stages") or {}
            if cur and isinstance(stages.get(cur), dict):
                _st = stages[cur]
                # XRUN-15: 已跑到 100% 的阶段是「完成的工作」，不因管线级取消/失败被改写成
                # cancelled（曾出现 status='cancelled' + progress=100 + '本体生成完成' 的自相矛盾）。
                if int(_st.get("progress") or 0) >= 100 and not _st.get("error"):
                    _st["status"] = "completed"
                else:
                    _st["status"] = status
                    _st["error"] = error
            cls.ensure_dirs(pipeline_id)
            from ..utils.atomic import write_json_atomic  # 唯一 tmp 名，消除与心跳/主存档的竞态
            write_json_atomic(cls.state_path(pipeline_id), data)
            return True

    @classmethod
    def mark_salvaged_completed(cls, pipeline_id: str, note: str) -> bool:
        """W9-1: 把「报告已完整落盘」的孤儿管线直接落成 completed（pipeline_health=degraded）。

        与 mark_failed 同款的轻量直写（load → 改键 → 原子替换，全程持锁）。语义：主报告
        （full_report.md + forecast.json）已经是完整交付物，中断只发生在其后的多种子集成/
        收尾窗口——整线判 failed 会把 $10+ 的成品当废品；改为 completed + degraded 健康块 +
        「集成已跳过」留痕。同时置 options.ensemble_done=True，使后续 resume 不再重烧集成窗口。
        """
        with cls._state_lock(pipeline_id):
            data = cls.load(pipeline_id)
            if not data or cls.INCOMPATIBLE_KEY in data:
                return False
            data["status"] = "completed"
            data["error"] = None
            data["global_progress"] = 100
            data["updated_at"] = _utcnow()
            opts = data.get("options")
            if not isinstance(opts, dict):
                opts = {}
                data["options"] = opts
            ph = opts.get("pipeline_health")
            if not isinstance(ph, dict):
                ph = {"status": "degraded", "issues": []}
            elif ph.get("status") == "ok":
                ph["status"] = "degraded"
            ph.setdefault("status", "degraded")
            issues = ph.get("issues")
            if not isinstance(issues, list):
                issues = []
            if note not in issues:
                issues.append(note)
            ph["issues"] = issues
            opts["pipeline_health"] = ph
            opts["ensemble_done"] = True   # 集成窗口被中断 → 按「已跳过」处理，不再重烧
            opts["ensemble_skipped"] = note
            opts["salvaged_orphan_at"] = _utcnow()
            stages = data.get("stages") or {}
            _rs = stages.get(STAGE_REPORT)
            if isinstance(_rs, dict):
                _rs["status"] = "completed"
                _rs["progress"] = 100
                _rs["error"] = None
                if not _rs.get("finished_at"):
                    _rs["finished_at"] = _utcnow()
                # 阶段消息可能冻结在「多种子集成 …」——替换为诚实的终态描述。
                if "多种子集成" in str(_rs.get("message") or ""):
                    _rs["message"] = "报告完成（多种子集成被中断，已跳过）"
            data["current_stage"] = STAGE_REPORT
            cls.ensure_dirs(pipeline_id)
            from ..utils.atomic import write_json_atomic
            write_json_atomic(cls.state_path(pipeline_id), data)
            return True

    @classmethod
    def touch_heartbeat(cls, pipeline_id: str, pid: Optional[int] = None) -> bool:
        """I-4-1: 仅刷新 heartbeat_at（+ 可选 owner_pid/owner_boot_id），不重建 dataclass。

        独立于阶段进度的「我还活着」壁钟信号，由 _run 的看护线程按固定节律调用。沿用
        mark_failed 的轻量直写模式（load → 改两三个键 → 原子替换），避免每次心跳走 full save
        / 触发 schema 迁移副作用。状态非 running 时静默 no-op（终态管线无需心跳）。
        """
        with cls._state_lock(pipeline_id):  # ORCH-5: 读改写全程持锁，防覆写主线程刚落盘的进度
            data = cls.load(pipeline_id)
            if not data or cls.INCOMPATIBLE_KEY in data:
                return False
            if data.get("status") != "running":
                return False
            data["heartbeat_at"] = _utcnow()
            if pid is not None:
                data["owner_pid"] = int(pid)
                data["owner_boot_id"] = _BOOT_ID
            cls.ensure_dirs(pipeline_id)
            # 高频心跳：唯一 tmp 名消除与主存档竞态；fsync=False（数秒即被覆写，无需落盘耐久）。
            from ..utils.atomic import write_json_atomic
            write_json_atomic(cls.state_path(pipeline_id), data, fsync=False)
            return True

    # ----------------------------------------------------------------------
    # I-4-3: 产物清单 manifest.json（每条产物的 sha256/字节数/产出阶段/schema_ok）。
    # 与 run.json（run manifest，可复现性快照）不同：此清单服务于「复用前完整性校验」，
    # 防止半写/截断的产物冒充 completed 被复用、把垃圾喂给下游静默降级整份预测。
    # ----------------------------------------------------------------------

    @classmethod
    def artifact_manifest_path(cls, pipeline_id: str) -> str:
        """I-4-3: uploads/pipelines/<id>/handoff/manifest.json 的路径。"""
        return os.path.join(cls.handoff_dir(pipeline_id), "manifest.json")

    @classmethod
    def load_artifact_manifest(cls, pipeline_id: str) -> dict[str, Any]:
        """I-4-3: 读产物清单（缺失/损坏 → 空 dict，按「无清单」降级到旧的存在性复用）。"""
        try:
            path = cls.artifact_manifest_path(pipeline_id)
        except ValueError:
            return {}
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def write_artifact_manifest(cls, pipeline_id: str, manifest: dict[str, Any]) -> None:
        """I-4-3: 原子写产物清单（复用 utils.atomic，与其它产物写法一致）。"""
        from ..utils.atomic import write_json_atomic
        cls.ensure_dirs(pipeline_id)
        write_json_atomic(cls.artifact_manifest_path(pipeline_id), manifest)

    @classmethod
    def load(cls, pipeline_id: str) -> Optional[dict[str, Any]]:
        try:
            path = cls.state_path(pipeline_id)
        except ValueError:
            return None  # malformed id → treat as not-found, keep callers robust
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        # I-4-4: 版本检查 + 迁移，在所有 load() 消费者之前统一进行。
        v = int(data.get("schema_version") or 1)
        if v > PIPELINE_SCHEMA_VERSION:
            # 文件比当前代码更新。严格模式（默认）下拒绝，避免按旧语义解析丢字段、
            # 并在下次落盘时把新文件降级覆写。返回带 pipeline_id 的最小哨兵 dict，
            # 让 status/list 仍能读到 id 而不至于崩，由 API 层映射为 409。
            strict = bool(getattr(Config, "PIPELINE_STRICT_SCHEMA", True))
            if strict:
                return {
                    "pipeline_id": data.get("pipeline_id", pipeline_id),
                    "status": data.get("status"),
                    "schema_version": v,
                    cls.INCOMPATIBLE_KEY: v,
                }
            # 非严格（应急旁路）：尽力按旧语义读取，不迁移、不改版本号。
            return data
        if v < PIPELINE_SCHEMA_VERSION:
            try:
                data = cls._migrate(data, v)
            except Exception:
                # 迁移意外失败：返回原始数据（from_dict 的逐字段默认仍可兜底），不丢记录。
                return data
        return data

    @classmethod
    def is_incompatible(cls, data: Optional[dict[str, Any]]) -> Optional[int]:
        """若 load() 返回的是「更新版本」哨兵，返回该文件的版本号，否则 None。"""
        if isinstance(data, dict) and cls.INCOMPATIBLE_KEY in data:
            try:
                return int(data[cls.INCOMPATIBLE_KEY])
            except (TypeError, ValueError):
                return PIPELINE_SCHEMA_VERSION + 1
        return None

    @classmethod
    def delete(cls, pipeline_id: str) -> bool:
        """删除一条管线记录（整个 uploads/pipelines/<id>/ 目录，含 handoff 产物）。

        只做文件系统删除；调用方（PipelineOrchestrator.delete_pipeline）负责
        拒绝在飞管线。目录不存在返回 False。
        """
        import shutil

        # 路径防御集中在 _dir/_validate_id（pipeline_id 来自 URL，绝不允许逃出数据目录）。
        try:
            target = cls._dir(pipeline_id)
        except ValueError:
            return False
        if not os.path.isdir(target):
            return False
        shutil.rmtree(target, ignore_errors=True)
        return not os.path.isdir(target)

    @classmethod
    def list_pipelines(cls) -> list[dict[str, Any]]:
        root = Config.PIPELINE_DATA_DIR
        if not os.path.isdir(root):
            return []
        out = []
        for pid in os.listdir(root):
            data = cls.load(pid)
            if data:
                out.append({
                    "pipeline_id": pid,
                    "status": data.get("status"),
                    "prompt": data.get("prompt"),
                    "global_progress": data.get("global_progress"),
                    "created_at": data.get("created_at"),
                    "report_id": data.get("report_id"),
                })
        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return out


# ---------------------------------------------------------------------------
# DeerFlow 子进程定位与启动
# ---------------------------------------------------------------------------


def _detect_deerflow_python(deerflow_dir: str) -> list[str]:
    """返回调用 DeerFlow 的命令前缀（不含脚本与参数）。

    优先级：显式 DEERFLOW_PYTHON > 探测 .venv > 退回 `uv run --project`。
    """
    if Config.DEERFLOW_PYTHON and os.path.exists(Config.DEERFLOW_PYTHON):
        return [Config.DEERFLOW_PYTHON]
    candidates = [
        os.path.join(deerflow_dir, "backend", ".venv", "bin", "python"),
        os.path.join(deerflow_dir, ".venv", "bin", "python"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return [c]
    # 退回 uv（较慢，且要求 uv 在 PATH）
    backend = os.path.join(deerflow_dir, "backend")
    return ["uv", "run", "--project", backend, "python"]


def _kill_process_group(proc: Optional[subprocess.Popen], sig: int = signal.SIGKILL) -> None:
    """终止子进程所在的整个进程组（与 SimulationRunner 一致，避免遗留孙子进程）。

    DeerFlow 子进程使用 start_new_session=True 自成进程组，因此 os.killpg 能连带
    清理它派生的任何子进程（如 stdio MCP server / sandbox shell）。失败时回退到
    仅终止直接子进程。
    """
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


# ORCH-1(5): 建图切块前的内嵌图片清洗。Markdown 形式 ![alt](data:image/...;base64,...)
# 与 HTML 形式 <img src="data:..."> 各一条正则；仅捕获 data-URI 本体，外部 URL 图片不动。
_MD_DATA_URI_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*data:[^)]*\)")
_HTML_DATA_URI_IMG_RE = re.compile(
    r"<img\b[^>]*\bsrc\s*=\s*[\"']data:[^\"']*[\"'][^>]*>", re.IGNORECASE)
_HTML_ALT_ATTR_RE = re.compile(r"\balt\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


def _strip_data_uri_images(text: str) -> str:
    """建图切块前剔除 base64/data-URI 内嵌图片，保留 alt 文本/图注。

    带图表的研究报告（图被渲染成 base64 PNG 内嵌）若原样切块，单张图就是成千上万
    字符的 base64 噪声 episode，会污染图谱实体抽取。此函数把 data-URI 图片本体替换
    为其 alt 文本（无 alt 则移除标签），正文与外部 URL 图片逐字节不动。
    GRAPH_STRIP_DATA_URI_IMAGES=false 可整体关闭（回到今日行为）；清洗过程任何异常
    也回退原文——degrade-safe，绝不因清洗失败阻断建图。
    （knob 就地读 env：config.py 归属其它 wave item，此处不新增 Config 属性。）
    """
    if os.environ.get("GRAPH_STRIP_DATA_URI_IMAGES", "true").strip().lower() == "false":
        return text
    try:
        stripped = _MD_DATA_URI_IMG_RE.sub(lambda m: m.group(1), text)

        def _img_alt(m: "re.Match[str]") -> str:
            alt = _HTML_ALT_ATTR_RE.search(m.group(0))
            return alt.group(1) if alt else ""

        return _HTML_DATA_URI_IMG_RE.sub(_img_alt, stripped)
    except Exception:  # noqa: BLE001 — 清洗只是降噪增强，失败回退原文
        return text


def _sync_deerflow_bridge_if_stale(deerflow_dir: str) -> None:
    """启动研究子进程前的漂移防护（2026-07-03 live-surfaced）。

    ``deerflow_bridge/``（仓库内、git 跟踪的源）与实际执行的
    ``$DEERFLOW_DIR/deerflow_research.py``（默认 ``deer-flow/``，setup.sh 从 bridge
    拷贝生成）是两份独立文件。此前所有对 deerflow_bridge 的修复（audit wave、
    actor-cast 纪律等）只有重新运行 ``./setup.sh`` 才会同步到部署副本——一次实机
    forecast run 验证时发现子进程仍在跑没有 enforce_actor_cast 的旧代码（3217 行
    vs 源 3792 行），导致 28-actor 的档案未按 ACTOR_CAST_MAX=20 截断。
    此函数在每次启动子进程前做一次内容哈希比对，检测到漂移就按 setup.sh 同款的
    ``cp`` 逻辑自动重新同步（deerflow_research.py + 两个种子 skill），并记录一条
    WARNING——不再依赖人工记得重跑 setup.sh。退化安全：找不到 bridge 目录（例如
    生产环境只部署了 deer-flow/、没有 bridge 源）或任何异常都仅记录警告，不阻断管线。
    """
    try:
        # backend/app/services/pipeline_orchestrator.py -> repo root 是三层上级，
        # 与 config.py 里 DEERFLOW_DIR 默认值的算法一致（同样以 __file__ 为基准）。
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        bridge_dir = os.path.join(repo_root, "deerflow_bridge")
        bridge_script = os.path.join(bridge_dir, "deerflow_research.py")
        deployed_script = os.path.join(deerflow_dir, "deerflow_research.py")
        if not os.path.isfile(bridge_script) or not os.path.isfile(deployed_script):
            return  # 没有 bridge 源（如纯生产部署）——无法比对，也无需比对

        def _digest(path: str) -> str:
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        pairs = [(bridge_script, deployed_script)]
        # ORCH-1(2): config-reflected tool modules registered in config.yaml as BARE
        # module names (`use: market_tools:...` / `search_tools:...` / `cached_fetch:...`).
        # deerflow_research.py runs with sys.path[0]==deer-flow/, so the harness
        # reflection resolver imports these by bare name — they MUST sit next to the
        # deployed script or web_search/web_fetch/prediction_market tools fail to load.
        # setup.sh copies them, but a bridge-only edit (no ./setup.sh rerun) would
        # otherwise drift exactly like deerflow_research.py did; mirror that guard here.
        for _tool_mod in ("market_tools.py", "search_tools.py", "cached_fetch.py"):
            _tool_src = os.path.join(bridge_dir, _tool_mod)
            if os.path.isfile(_tool_src):
                pairs.append((_tool_src, os.path.join(deerflow_dir, _tool_mod)))
        # W9-9：MCP 扩展注册文件也同步到部署目录。该文件是 OPT-IN 的（harness 仅在
        # DEER_FLOW_EXTENSIONS_CONFIG_PATH 指向它时才加载），部署副本本身惰性无副作用；
        # DeerFlowResearchRunner 在「图谱已存在」的路径把该 env 指向此部署副本。
        _ext_src = os.path.join(bridge_dir, "extensions_config.json")
        if os.path.isfile(_ext_src):
            pairs.append((_ext_src, os.path.join(deerflow_dir, "extensions_config.json")))
        # ORCH-1(1): skill 同步从硬编码 2 个种子 skill 改为 glob 全量枚举——此前 bridge 侧
        # 新增第 3 个 skill 只有重跑 ./setup.sh 才会部署，这里会静默漂移。与 setup.sh 语义
        # 一致：仅当部署侧存在 skills/public/ 时才同步；目标 skill 目录缺失（新 skill 首次
        # 部署）则按 setup.sh 的 mkdir -p 逻辑创建后拷贝。已存在的目标沿用内容哈希比对。
        import glob as _glob
        public_skills_dir = os.path.join(deerflow_dir, "skills", "public")
        if os.path.isdir(public_skills_dir):
            for src in sorted(_glob.glob(os.path.join(bridge_dir, "skills", "*", "SKILL.md"))):
                skill = os.path.basename(os.path.dirname(src))
                pairs.append((src, os.path.join(public_skills_dir, skill, "SKILL.md")))
            # GATE-W9：技能捆绑脚本（如 forecast-visuals/scripts/render.py）也随 SKILL.md
            # 一并同步——渲染器只部署 SKILL.md 时确定性图表步会静默降级为「renderer missing」。
            for src in sorted(_glob.glob(os.path.join(bridge_dir, "skills", "*", "scripts", "*.py"))):
                skill_dir = os.path.dirname(os.path.dirname(src))
                skill = os.path.basename(skill_dir)
                pairs.append((src, os.path.join(public_skills_dir, skill, "scripts", os.path.basename(src))))

        synced = []
        for src, dst in pairs:
            if os.path.isfile(dst) and _digest(src) == _digest(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            synced.append(os.path.relpath(dst, deerflow_dir))

        # ORCH-1(1) config.yaml：setup.sh 明确「绝不覆盖已存在的 deer-flow/config.yaml」
        # （部署侧可能有本地 provider stanza 改动），故此处只做漂移检测 + 提示手工 diff，
        # 不自动拷贝——与 setup.sh 的 never-clobber 语义保持一致。
        bridge_cfg = os.path.join(bridge_dir, "config.yaml")
        deployed_cfg = os.path.join(deerflow_dir, "config.yaml")
        if (os.path.isfile(bridge_cfg) and os.path.isfile(deployed_cfg)
                and _digest(bridge_cfg) != _digest(deployed_cfg)):
            logger.warning(
                "deer-flow/config.yaml 与 deerflow_bridge/config.yaml 内容不一致（按 setup.sh "
                "约定不自动覆盖——部署副本可能包含本地 provider 改动）。请手工 diff 两份文件"
                "以拾取新的 provider stanza。")
        if synced:
            logger.warning(
                "DeerFlow 部署副本与 deerflow_bridge/ 源不同步，已自动重新同步（%s）——"
                "研究子进程刚才会用旧代码运行。建议提交后运行一次 ./setup.sh 使其保持最新。",
                ", ".join(synced),
            )
    except Exception as sync_err:  # noqa: BLE001 — 漂移防护本身绝不能阻断研究阶段
        logger.warning("DeerFlow 部署副本漂移检测失败（忽略，不影响本次运行）: %s", sync_err)


class DeerFlowResearchRunner:
    """启动 deerflow_research.py 子进程并把进度回传给回调。"""

    # 在飞的研究子进程，供后端关闭时统一清理（避免 prompt→预测 期间被孤儿化、继续烧额度）。
    _live_procs: "set[subprocess.Popen]" = set()

    @classmethod
    def cleanup_all(cls) -> None:
        """后端退出时终止所有仍在运行的研究子进程组（SIGTERM，温和优先）。"""
        for proc in list(cls._live_procs):
            if proc.poll() is None:
                _kill_process_group(proc, signal.SIGTERM)
            cls._live_procs.discard(proc)

    @staticmethod
    def run(
        prompt: str,
        handoff_dir: str,
        *,
        on_progress: Callable[[int, str], None],
        depth: Optional[str] = None,
        model: Optional[str] = None,
        language: Optional[str] = None,
        subagents: Optional[bool] = None,
        timeout: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        resume: bool = False,
        kg_graph_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """运行研究子进程，阻塞直到结束。返回 handoff 摘要。

        ITEM-3 ``resume``：True 时给子进程加 ``--resume``——bridge 若在 handoff_dir 找到
        question_hash 匹配的 research_checkpoint.json，就复用其 LangGraph thread、跳过已完成
        pass、从下一 pass 续跑（否则全量重启）。调用方仅在既存 checkpoint 时置 True（崩溃/超时
        重试路径），故正常首跑逐字节不变。

        W9-9 ``kg_graph_id``：非空且 RESEARCH_MCP_KG 开启时，把 DEER_FLOW_EXTENSIONS_CONFIG_PATH
        指向部署目录的 extensions_config.json 并注入 DRF_MCP_KG_GRAPH_ID——研究子进程可经 MCP
        （kg_search/kg_trace_cascade 等）直查既有基线图谱。调用方仅在图谱已存在的路径
        （fork/continue/resume-with-graph）传入；首跑传 None，env 与今日逐字节一致。

        Raises:
            PipelineCancelled: cancel_event 被置位（用户取消），子进程组已被终止。
            RuntimeError: 子进程失败、超时或未产出报告。
        """
        deerflow_dir = Config.DEERFLOW_DIR
        script = os.path.join(deerflow_dir, "deerflow_research.py")
        if not os.path.isdir(deerflow_dir):
            raise RuntimeError(f"DeerFlow 目录不存在: {deerflow_dir}（设置 DEERFLOW_DIR）")
        if not os.path.exists(script):
            raise RuntimeError(f"未找到 deerflow_research.py: {script}")
        _sync_deerflow_bridge_if_stale(deerflow_dir)

        os.makedirs(handoff_dir, exist_ok=True)
        # 把（可能敏感的）研究问题写入文件，经 --prompt-file 传给子进程，避免出现在
        # ps / /proc/<pid>/cmdline 里（EXECPLAN2 F-0-7/F-13-3，与 llm_client 既有约定一致）。
        # run() 会阻塞到子进程退出，子进程启动时即读取该文件，故可在 finally 安全删除。
        fd, prompt_file = tempfile.mkstemp(prefix=".prompt-", suffix=".txt", dir=handoff_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as _pf:
            _pf.write(prompt)
        try:
            os.chmod(prompt_file, 0o600)
        except OSError:
            pass
        cmd = _detect_deerflow_python(deerflow_dir) + [
            script,
            "--prompt-file", prompt_file,
            "--out-dir", handoff_dir,
            "--model", model or Config.DEERFLOW_MODEL,
            "--depth", depth or Config.DEERFLOW_RESEARCH_DEPTH,
        ]
        lang = language if language is not None else Config.DEERFLOW_RESEARCH_LANGUAGE
        if lang:
            cmd += ["--target-language", lang]
        if (subagents if subagents is not None else Config.DEERFLOW_SUBAGENTS):
            cmd += ["--subagents"]
        # ITEM-3：崩溃/超时重试时若 handoff_dir 已有 checkpoint，续跑而非全量重启。
        if resume:
            cmd += ["--resume"]

        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        # T6.6: deep 开场 pass 的递归上限从 Config 单一真源下发给子进程（旧版在 bridge 内直接读
        # os.environ）。Config 属性本身读 env（默认 220），故此处覆盖即「Config 即真源」。
        env["DEERFLOW_DEEP_OPENING_RECURSION_LIMIT"] = str(Config.DEERFLOW_DEEP_OPENING_RECURSION_LIMIT)
        # 双轨研究开关：Config 即单一真源（默认 True）下发给子进程，决定 deerflow 是否在跑
        # Track A（深度研究→research_report.md）的同时并行跑 Track B（角色本体研究→actor_dossier.md）。
        # 关闭时子进程行为与今日逐字节一致（只跑 Track A，不产出 actor_dossier.md）。
        env["DEERFLOW_DUAL_TRACK"] = "true" if getattr(Config, "DEERFLOW_DUAL_TRACK", True) else "false"
        # CONF-1：深度扇出开关/宽度同样从 Config 单一真源下发给 bridge（bridge 侧经
        # _env_flag/os.environ 读取，自带默认值）。不下发时 Config 里的默认（true/8）对
        # 研究子进程静默失效（与 ACTOR_CAST_MAX 曾经的漂移同因）。
        env["RESEARCH_DEEP_FANOUT"] = (
            "true" if getattr(Config, "RESEARCH_DEEP_FANOUT", True) else "false")
        env["RESEARCH_FANOUT_WIDTH"] = str(getattr(Config, "RESEARCH_FANOUT_WIDTH", 8))
        # ACTOR-CAST discipline：主角色上限 + 媒体降级从 Config 单一真源下发给 bridge
        # （抽取提示词的 actor 范围、enforce_actor_cast 的截断/降级都按此执行）。
        env["ACTOR_CAST_MAX"] = str(getattr(Config, "ACTOR_CAST_MAX", 20))
        env["ACTOR_EXCLUDE_MEDIA"] = "true" if getattr(Config, "ACTOR_EXCLUDE_MEDIA", True) else "false"
        # ORCH-1(3): Polymarket 预测市场快照参数同样从 Config 单一真源下发给 bridge
        # （bridge 侧经 _env_flag/os.environ 读取）。不下发时子进程只看到自己的 env 默认值，
        # 后端 .env 里的配置对研究子进程静默失效（与 ACTOR_CAST_MAX 曾经的漂移同因）。
        env["PREDICTION_MARKETS_ENABLED"] = (
            "true" if getattr(Config, "PREDICTION_MARKETS_ENABLED", True) else "false")
        env["PREDICTION_MARKETS_MAX"] = str(getattr(Config, "PREDICTION_MARKETS_MAX", 20))
        env["PREDICTION_MARKETS_MIN_VOLUME"] = str(getattr(Config, "PREDICTION_MARKETS_MIN_VOLUME", 200.0))
        env["PREDICTION_MARKETS_MAX_PER_EVENT"] = str(getattr(Config, "PREDICTION_MARKETS_MAX_PER_EVENT", 3))
        # W9-9：图谱已存在（fork/continue/resume-with-graph）时把 KG MCP server 暴露给研究子进程。
        # extensions_config.json 由 _sync_deerflow_bridge_if_stale 同步到部署目录；两台 server 均
        # 设计为图谱缺失时返回结构化错误、绝不 hang（见该文件 _comment），接线本身 degrade-safe。
        if kg_graph_id and bool(getattr(Config, "RESEARCH_MCP_KG", True)):
            _ext_cfg = os.path.join(deerflow_dir, "extensions_config.json")
            if os.path.isfile(_ext_cfg):
                env["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = _ext_cfg
                env["DRF_MCP_KG_GRAPH_ID"] = str(kg_graph_id)
                logger.info("研究子进程已接入 KG MCP（graph_id=%s）", kg_graph_id)
            else:
                logger.warning(
                    "RESEARCH_MCP_KG 开启且 graph_id=%s，但部署目录缺 extensions_config.json"
                    "——跳过 MCP 接线（研究照常运行）", kg_graph_id)

        logger.info(f"启动 DeerFlow 研究子进程: {' '.join(cmd[:1])} … (cwd={deerflow_dir})")
        on_progress(2, "启动深度研究子进程…")

        proc = subprocess.Popen(
            cmd,
            cwd=deerflow_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # 自成进程组，便于 os.killpg 连带清理孙子进程
        )
        DeerFlowResearchRunner._live_procs.add(proc)
        if on_spawn is not None:
            try:
                on_spawn(proc.pid)
            except Exception:  # noqa: BLE001 — PID 持久化失败不影响研究本身
                logger.warning("研究子进程 PID 持久化失败", exc_info=True)

        # 看门狗预算按研究深度缩放：deep 是多轮研究协议（source map →
        # primary evidence → actors → contradictions → forecast implications →
        # synthesis），在固定 2400s 下经常被无差别 SIGKILL。优先级（T6.6）：显式 timeout 参数 >
        # 用户在 .env 里显式设置的 DEERFLOW_RESEARCH_TIMEOUT > Config.deerflow_depth_budget() 档位默认值
        # （CONF-1：档位值在双轨/子代理/扇出开启时 ×1.5，避免并行模式下更重的协议被无差别 SIGKILL）。
        effective_depth = (depth or Config.DEERFLOW_RESEARCH_DEPTH or "standard").lower()
        if timeout:
            budget = timeout
        elif os.environ.get("DEERFLOW_RESEARCH_TIMEOUT", "").strip():
            budget = Config.DEERFLOW_RESEARCH_TIMEOUT
        else:
            budget = Config.deerflow_depth_budget(effective_depth)
        deadline = time.time() + budget
        # 看门狗：即使子进程长时间无输出（模型思考），也能在超时后被杀掉。
        timed_out = {"hit": False}

        def _watchdog():
            if proc.poll() is None:
                timed_out["hit"] = True
                _kill_process_group(proc)

        watchdog = threading.Timer(budget, _watchdog)
        watchdog.daemon = True
        watchdog.start()

        # 取消监视：用户取消时立刻杀掉整个研究子进程组（不等超时）。
        # 单独线程而非读循环检查，因为读循环可能阻塞在无输出的 readline 上。
        cancelled = {"hit": False}
        if cancel_event is not None:
            def _cancel_watcher():
                while proc.poll() is None:
                    if cancel_event.wait(timeout=1.0):
                        cancelled["hit"] = True
                        _kill_process_group(proc)
                        return

            t_cancel = threading.Thread(target=_cancel_watcher, daemon=True)
            t_cancel.start()

        # 启发式进度：研究阶段难以精确，按事件类型缓慢推进 2→95。
        local = 2
        tool_events = 0
        last_line = ""
        # I-5-7: 结构化研究阶段遥测（token/工具量/壁钟）。bridge 已在 stdout 发出 [usage] 行，
        # 此前只用于推进进度启发，token 数字被丢弃；这里顺带累加，使最贵的研究阶段也进入统一计量。
        _tok_in = _tok_out = _tok_total = 0
        _result_events = 0
        _t_start = time.time()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                if not line:
                    continue
                last_line = line
                # 解析进度日志的事件类型 [tool]/[result]/[stage]/[ok]/[done]/[error]/[usage]
                if "[tool]" in line:
                    tool_events += 1
                    local = min(90, 10 + tool_events * 4)
                    on_progress(local, _tail(line))
                elif "[result]" in line:
                    _result_events += 1
                    on_progress(local, _tail(line))
                elif "[stage]" in line:
                    on_progress(min(local, 92), _tail(line))
                elif "[usage]" in line:
                    # I-5-7: 仅累加 token，不动进度（usage 行非进度信号）。
                    parsed = _parse_usage_line(line)
                    if parsed is not None:
                        _i, _o, _t = parsed
                        _tok_in += _i
                        _tok_out += _o
                        _tok_total += (_t if _t else _i + _o)
                elif "[ok]" in line or "[done]" in line:
                    local = max(local, 95)
                    on_progress(local, _tail(line))
                elif "[error]" in line:
                    on_progress(local, _tail(line))
                elif "[init]" in line:
                    on_progress(max(local, 4), _tail(line))
                if timed_out["hit"] or time.time() > deadline:
                    break
            try:
                returncode = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # W9-4：stdout 已经 EOF 但子进程组 30s 内没退出（bridge 的孙子进程/线程收尾慢）。
                # 旧行为让 TimeoutExpired 直接冒泡 → 整条轨被当失败丢弃——pipe_0f2bee 因此把
                # 2/3 条健康续跑轨（报告+checkpoint 都已落盘）判死、降级到只剩基率轨。
                # 改为：先杀进程组，再按盘上产物判定——research_report.md 完整且结构化产物/
                # checkpoint 表明协议已推进 → 该轨按存活处理；否则仍按失败抛出（信息更明确）。
                _kill_process_group(proc)
                try:
                    returncode = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    returncode = -9  # 进程组已被 SIGKILL，wait 仍超时视作强杀
                if _track_artifacts_survived(handoff_dir):
                    logger.warning(
                        "研究子进程收尾超时（stdout 已关闭但进程组未在 30s 内退出，exit=%s）"
                        "——盘上产物完整，按存活轨继续", returncode,
                    )
                    on_progress(95, "子进程收尾超时，但研究产物完整——按存活轨继续")
                    returncode = 0
                else:
                    raise RuntimeError(
                        "研究子进程收尾超时（30s）且产物不完整"
                        "（research_report.md 缺失/过短，或无结构化产物/checkpoint）——该轨失败"
                    )
        finally:
            watchdog.cancel()
            if proc.poll() is None:
                _kill_process_group(proc)
            DeerFlowResearchRunner._live_procs.discard(proc)
            # 子进程已退出（或被杀），prompt 文件不再需要，删除以减小磁盘驻留面。
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

        if cancelled["hit"]:
            raise PipelineCancelled("深度研究已取消")

        report_path = os.path.join(handoff_dir, "research_report.md")
        if timed_out["hit"]:
            # 超时打捞：研究主报告先于 actors/sources 提取阶段落盘——若被看门狗
            # 杀掉时报告已经写出，没必要丢弃整轮研究，降级继续（仅缺结构化档案）。
            if os.path.exists(report_path) and len(_read_text(report_path).strip()) >= 400:
                logger.warning(
                    f"DeerFlow 研究超时（>{budget}s），但 research_report.md 已写出——打捞继续"
                )
                on_progress(95, "研究超时，但报告已生成——打捞继续（无结构化档案）")
                # ITEM-14：报告已落盘但结构化抽取还没跑（'无结构化档案'）。花一个有界的
                # --extract-only 子进程把 actors/sources/timeline/quantitative 抢救出来，
                # 让下游本体/图谱/模拟仍有真实结构可用，而非带着空档案降级。best-effort。
                actors_path = os.path.join(handoff_dir, "actors.json")
                _salvage_on = os.environ.get(
                    "RESEARCH_EXTRACT_ONLY_SALVAGE", "true").strip().lower() not in ("0", "false", "no")
                if _salvage_on and not os.path.exists(actors_path):
                    try:
                        _run_extract_only_salvage(
                            deerflow_dir, handoff_dir, prompt, model or Config.DEERFLOW_MODEL,
                            effective_depth, language, env, on_progress,
                        )
                    except Exception as _sv_err:  # noqa: BLE001 — 打捞绝不令研究失败
                        logger.warning("ITEM-14：extract-only 打捞异常（降级继续）: %s", _sv_err)
            else:
                raise RuntimeError(
                    f"DeerFlow 研究超时（>{budget}s，depth={effective_depth}）。"
                    "可降低研究深度，或在 .env 设更大的 DEERFLOW_RESEARCH_TIMEOUT"
                )
        if returncode != 0 and not os.path.exists(report_path):
            raise RuntimeError(f"DeerFlow 研究子进程失败 (exit={returncode})：{last_line}")
        if not os.path.exists(report_path):
            raise RuntimeError("DeerFlow 研究未产出 research_report.md")

        report = _read_text(report_path)
        if not report.strip():
            raise RuntimeError("research_report.md 为空")
        # 纵深防御：即便上游漏写了降级/错误消息当报告，也别让管线拿一段错误串去
        # 建图/模拟/写报告（那会把垃圾当成功）。覆盖 DeerFlow 降级文案、原始 provider
        # 报错、以及 MiniMax 域内容审核(422 new_sensitive)等多种短错误串。
        # RES-1/RES-8 镜像：除「短 + 错误串」外再加一道裸长度下限（RESEARCH_MIN_REPORT_CHARS，
        # 默认 400，0 关闭）——旧版 bridge 部署仍在时，编排器侧兜住 <400 字符的报告残段。
        _min_chars = int(getattr(Config, "RESEARCH_MIN_REPORT_CHARS", 400) or 0)
        _rlen = len(report.strip())
        if (_min_chars and _rlen < _min_chars) or (
                _rlen < 400 and any(m in report for m in _LLM_ERROR_MARKERS)):
            raise RuntimeError(
                "DeerFlow 返回的是 LLM 降级/错误消息而非研究报告"
                "（提供方临时不可用/限流/额度、网络错误，或内容审核拦截），"
                "请稍后重试、降低研究深度，或更换模型"
            )

        # 双轨 Track B 产物：角色本体档案。旗标关闭或 Track B 未产出时文件缺失，
        # _read_text 返回 ""，下游按「空即退化」处理（document_texts/chunks 与单轨逐字节一致）。
        # RES-8 镜像：错误串/过短的卷宗按缺失处理（bridge 的 _is_degraded_artifact 同语义）。
        actor_dossier = _read_text(os.path.join(handoff_dir, "actor_dossier.md"))
        if actor_dossier and _is_degraded_dossier(actor_dossier):
            logger.warning("actor_dossier.md 疑似降级产物（错误串/过短），按缺失处理")
            actor_dossier = ""
        actors = _read_json(os.path.join(handoff_dir, "actors.json"))
        sources = _read_json(os.path.join(handoff_dir, "sources.json"))
        timeline = _read_json(os.path.join(handoff_dir, "timeline.json"))
        # I-5-7: 汇总研究阶段遥测。token 行可能整轮缺失（某些研究模型不报 usage）→ 全 0/None。
        research_telemetry = {
            "model": (model or Config.DEERFLOW_MODEL),
            "depth": effective_depth,
            "tokens_in": _tok_in,
            "tokens_out": _tok_out,
            "tokens_total": _tok_total or (_tok_in + _tok_out),
            "tool_calls": tool_events,
            "results": _result_events,
            "wall_s": round(time.time() - _t_start, 1),
        }
        on_progress(100, f"研究完成（报告 {len(report)} 字）")
        return {
            "report": report,
            "report_path": report_path,
            "actor_dossier": actor_dossier,  # 双轨 Track B：角色本体档案（缺失为 ""）
            "actors": actors,
            "sources": sources,
            "timeline": timeline,
            "exit_code": returncode,
            "research_telemetry": research_telemetry,  # I-5-7
        }


# I-5-7: 解析 DeerFlow bridge 已发出的「[usage] tokens in=.. out=.. total=..」行
# （deerflow_research.py:ProgressLog.write('usage', ...)）。容错：out/total 可能为 None
# 时 bridge 会打印字面 "None"，故各组匹配数字或 None；no-match → 该行不计入。
_USAGE_RE = re.compile(
    r"tokens in=(?P<in>\d+|None)\s+out=(?P<out>\d+|None)\s+total=(?P<total>\d+|None)"
)


def _parse_usage_line(line: str) -> Optional[tuple[int, int, int]]:
    """从一行 [usage] 日志解析 (in, out, total) token；解析不出返回 None。"""
    m = _USAGE_RE.search(line)
    if not m:
        return None

    def _num(s: str) -> int:
        return int(s) if s and s.isdigit() else 0

    return _num(m.group("in")), _num(m.group("out")), _num(m.group("total"))


def _tail(s: str, limit: int = 160) -> str:
    s = s.strip()
    # 去掉时间戳+级别前缀，保留信息部分
    if "] " in s:
        s = s.split("] ", 1)[1] if s.count("] ") >= 1 else s
    return s if len(s) <= limit else s[:limit] + "…"


# RES-1/RES-8: LLM 降级/错误串标记（live 报告守卫 + 卷宗降级门共用；单一真源）。
_LLM_ERROR_MARKERS = (
    "The configured LLM provider",  # DeerFlow LLMErrorHandlingMiddleware 降级
    "LLM request failed",            # 原始 provider 报错被当成正文
    "unprocessable_entity",          # 例如 MiniMax 422 内容审核
    "new_sensitive",                 # MiniMax 域内容过滤命中(code 1026)
    "Error code: 4", "Error code: 5",  # 4xx/5xx 错误串
)


def _is_degraded_dossier(text: str) -> bool:
    """RES-8 编排器侧镜像：actor_dossier.md 含错误串或短于 RESEARCH_MIN_REPORT_CHARS
    （默认 400，0 关闭长度门）即视为降级产物——调用方应按缺失处理，绝不喂给本体/建图。"""
    t = (text or "").strip()
    if not t:
        return False  # 空 = 缺失，由调用方原有语义处理
    _min = int(getattr(Config, "RESEARCH_MIN_REPORT_CHARS", 400) or 0)
    if _min and len(t) < _min:
        return True
    return any(m in t for m in _LLM_ERROR_MARKERS)


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _read_json(path: str) -> Optional[Any]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ITEM-3：bridge 每完成一个研究 pass 就在 handoff_dir 落一个 research_checkpoint.json。崩溃/
# 超时重试时若它存在（且非空），就给重试的研究子进程加 --resume 复用 LangGraph 线程、跳过已完成
# pass，而非从 pass 0 全量重启（一整轮 deep 研究的数十个已抓来源不再被白白丢弃）。空文件/读失败
# 一律当作「无 checkpoint」→ 全量重启（degrade-safe）。
def _has_research_checkpoint(handoff_dir: str) -> bool:
    """handoff_dir 是否有一个可用于续跑的 research_checkpoint.json（存在且非空）。"""
    try:
        p = os.path.join(handoff_dir, "research_checkpoint.json")
        return os.path.exists(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def _track_artifacts_survived(handoff_dir: str) -> bool:
    """W9-4（纯，可单测）：研究子进程「收尾超时」后的产物判定——该轨是否实际已成功。

    判据：research_report.md 已落盘且非降级短串（≥400 字符，与 _load_research_handoff 同
    语义），且（actors.json 已产出 或 research_checkpoint.json 带非空 completed_passes）
    ——两者之一即证明研究协议真实推进过，报告不是半写残段。全部满足 → 该轨按存活处理。
    """
    report = _read_text(os.path.join(handoff_dir, "research_report.md")).strip()
    if len(report) < 400:
        return False
    if os.path.exists(os.path.join(handoff_dir, "actors.json")):
        return True
    ckpt = _read_json(os.path.join(handoff_dir, "research_checkpoint.json"))
    return bool(isinstance(ckpt, dict) and ckpt.get("completed_passes"))


def _run_extract_only_salvage(
    deerflow_dir: str, handoff_dir: str, prompt: str,
    model: str, depth: str, language: Optional[str], env: dict,
    on_progress: Callable[[int, str], None],
) -> bool:
    """ITEM-14：watchdog 超时打捞——用一个有界（默认 10 分钟）--extract-only 子进程，对已落盘的
    research_report.md 补跑结构化抽取 + 预测市场，恢复 actors/sources/timeline/quantitative。

    研究主 turn 被看门狗 SIGKILL 时，research_report.md 常已落盘、但 Stage 2 结构化抽取还没跑
    （'无结构化档案'）。此前只能带着空 actors/sources 降级继续；这里花一个短子进程把结构化档案
    抢救出来，让下游本体/图谱/模拟仍有真实的 actor/source 可用。

    有界（RESEARCH_EXTRACT_ONLY_TIMEOUT，默认 600s）、best-effort：任何失败/超时只记一行日志并
    返回 False，绝不让打捞本身把已成功的研究翻成失败。成功（exit 0）返回 True。
    """
    script = os.path.join(deerflow_dir, "deerflow_research.py")
    if not os.path.exists(script):
        return False
    try:
        _timeout = max(1, int(os.environ.get("RESEARCH_EXTRACT_ONLY_TIMEOUT", "600") or "600"))
    except ValueError:
        _timeout = 600

    # prompt 来源：优先复用 bridge 落在 handoff_dir 的 prediction_requirement.txt（不暴露在
    # cmdline，与 --prompt-file 既有约定一致）；缺失则写一个 0600 临时文件。
    _tmp_prompt: Optional[str] = None
    req_file = os.path.join(handoff_dir, "prediction_requirement.txt")
    if os.path.exists(req_file) and os.path.getsize(req_file) > 0:
        prompt_file = req_file
    else:
        fd, _tmp_prompt = tempfile.mkstemp(prefix=".extract-prompt-", suffix=".txt", dir=handoff_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as _pf:
            _pf.write(prompt or "")
        try:
            os.chmod(_tmp_prompt, 0o600)
        except OSError:
            pass
        prompt_file = _tmp_prompt

    cmd = _detect_deerflow_python(deerflow_dir) + [
        script,
        "--extract-only",
        "--prompt-file", prompt_file,
        "--out-dir", handoff_dir,
        "--model", model or Config.DEERFLOW_MODEL,
        "--depth", depth or Config.DEERFLOW_RESEARCH_DEPTH,
    ]
    _lang = language if language is not None else Config.DEERFLOW_RESEARCH_LANGUAGE
    if _lang:
        cmd += ["--target-language", _lang]

    logger.info("ITEM-14：启动 --extract-only 打捞子进程（≤%ds）以恢复结构化档案", _timeout)
    try:
        on_progress(96, "超时打捞：补跑结构化抽取（actors/sources/timeline）…")
    except Exception:  # noqa: BLE001 — 进度回调失败不影响打捞
        pass
    try:
        r = subprocess.run(
            cmd, cwd=deerflow_dir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=_timeout, start_new_session=True,
        )
        ok = (r.returncode == 0)
        if ok:
            logger.info("ITEM-14：--extract-only 打捞成功（结构化档案已补出）")
        else:
            logger.warning("ITEM-14：--extract-only 打捞退出码 %s（保留已有报告，降级继续）", r.returncode)
        return ok
    except subprocess.TimeoutExpired:
        logger.warning("ITEM-14：--extract-only 打捞超时（>%ds），降级继续", _timeout)
        return False
    except (OSError, subprocess.SubprocessError) as _e:
        logger.warning("ITEM-14：--extract-only 打捞失败（%s），降级继续", _e)
        return False
    finally:
        if _tmp_prompt is not None:
            try:
                os.unlink(_tmp_prompt)
            except OSError:
                pass


def _stage_walls(state: "PipelineState") -> dict[str, float]:
    """ITEM-18：从各阶段的 started_at→finished_at 时间戳算出每阶段墙钟秒数（stage → seconds）。

    供 build_stage_telemetry 把编排器的阶段级墙钟与 LLMMeter 的 token/成本合并。仅在两端时间戳
    都存在且差值非负时计入（未开始/未结束/时钟回拨 → 跳过，degrade-safe，绝不抛）。"""
    walls: dict[str, float] = {}
    for name, st in (getattr(state, "stages", None) or {}).items():
        started = _parse_iso(getattr(st, "started_at", None))
        finished = _parse_iso(getattr(st, "finished_at", None))
        if started is None or finished is None:
            continue
        dt = (finished - started).total_seconds()
        if dt >= 0:
            walls[str(name)] = dt
    # W9-2：多种子集成不占 stages（避免污染阶段带/前端渲染），墙钟从 options.ensemble_wall
    # 补入——此前 report 完成后的 +1h31m 集成窗口不归属任何阶段带（遥测里凭空消失）。
    try:
        ew = (getattr(state, "options", None) or {}).get("ensemble_wall") or {}
        e0, e1 = _parse_iso(ew.get("started_at")), _parse_iso(ew.get("finished_at"))
        if e0 is not None and e1 is not None:
            edt = (e1 - e0).total_seconds()
            if edt >= 0:
                walls["ensemble"] = edt
    except Exception:  # noqa: BLE001 — 纯观测，绝不抛
        pass
    return walls


def _load_research_handoff(handoff_dir: str) -> dict[str, Any]:
    """Load a previously generated DeerFlow handoff, tolerating missing optional JSON.

    Resume uses this to continue a failed pipeline when the expensive markdown
    dossier was already written but later structured extraction or the watchdog
    failed. The same short-error guard used by DeerFlowResearchRunner still
    applies, so provider fallback text is never treated as usable research.
    """
    report_path = os.path.join(handoff_dir, "research_report.md")
    report = _read_text(report_path)
    # 与 DeerFlowResearchRunner 的短报告守卫语义一致：LLM 降级/错误文案都是短串，
    # <400 字符一律拒绝（涵盖错误串），≥400 视为真实研究报告。
    if len(report.strip()) < 400:
        raise RuntimeError("已有研究报告缺失或过短，无法从研究阶段恢复")
    # RES-8 镜像（resume 路径）：错误串/过短的卷宗按缺失处理，与 live 路径一致。
    _dossier = _read_text(os.path.join(handoff_dir, "actor_dossier.md"))
    if _dossier and _is_degraded_dossier(_dossier):
        logger.warning("resume：actor_dossier.md 疑似降级产物（错误串/过短），按缺失处理")
        _dossier = ""
    return {
        "report": report,
        "report_path": report_path,
        # 双轨 Track B 产物：角色本体档案（actor_dossier.md）。缺失时 _read_text 返回 ""，
        # 下游按「空即退化」处理，与单轨/旗标关闭时逐字节一致。
        "actor_dossier": _dossier,
        "actors": _read_json(os.path.join(handoff_dir, "actors.json")),
        "sources": _read_json(os.path.join(handoff_dir, "sources.json")),
        "timeline": _read_json(os.path.join(handoff_dir, "timeline.json")),
        "exit_code": None,
        "resumed": True,
    }


# ===========================================================================
# PAR-2: 多角度并行研究轨 —— 角度特化前缀 + 确定性合并（纯函数，可单测）
# ---------------------------------------------------------------------------
# 研究阶段并行跑 K 条角度特化的研究轨（每条一个 DeerFlowResearchRunner 子进程，写入
# handoff/track_<k>/），随后把各轨产物确定性地合并回 handoff/，下游各阶段读合并后的
# handoff/ 与今日完全一致。轨 1 = 基线证据扫描（原始 brief 逐字，无前缀）；轨 2/3 = 附加
# 角度前缀。合并规则见各 merge_* 函数 docstring。
# ===========================================================================

# 角度定义：(short_key, H1 标题, prompt 前缀)。轨 1 前缀为空 → track_prompt == 原始 prompt
# 逐字节（RESEARCH_PARALLEL_TRACKS=1 时整条路径与今日一致）。上限即本列表长度。
_TRACK2_PREFIX = (
    "【研究视角：基率、参照类与历史类比】在回答下述预测问题时，请以「参照类预测"
    "（reference-class forecasting）」为主线：系统识别与本问题结构相似的历史先例与可比"
    "事件，估计相关基率（base rates）、历史发生频率与典型时间尺度，用它们为每个可能结局"
    "建立外部视角（outside view）的锚。优先搜集可量化的历史类比、长周期统计与既往类似情形"
    "的实际结局分布，而非仅当下叙事。\n\n原始预测问题：\n"
)
_TRACK3_PREFIX = (
    "【研究视角：行为者激励、反面论证与市场定价】在回答下述预测问题时，请聚焦三条线："
    "(1) 关键行为者的激励结构与约束——谁想要什么、面临何种约束、其理性策略如何影响结局；"
    "(2) 反面/证伪论证——主动搜寻能推翻主流叙事的反向证据、被忽视的风险与非共识情景"
    "（contrarian case）；(3) 市场定价信号——预测市场、期货、赔率、隐含概率等对相关结局的"
    "定价。优先搜集能挑战共识的一手证据与可交易的定价信号。\n\n原始预测问题：\n"
)

# (short_key, H1 标题, prefix)。合并后的 research_report.md 用 H1 标题作章节头。
_RESEARCH_TRACK_ANGLES: list[tuple[str, str, str]] = [
    ("base-evidence", "基线证据扫描 (Base Evidence Sweep)", ""),
    ("base-rates", "基率·参照类·历史类比 (Base Rates & Reference Classes)", _TRACK2_PREFIX),
    ("incentives-contrarian",
     "行为者激励·反面论证·市场定价 (Incentives, Contrarian Case & Market Pricing)",
     _TRACK3_PREFIX),
]


def _track_tier_rank(tier: Any) -> int:
    """来源层级 → 排序秩：S1 最高（rank 1）… S4（rank 4），缺失/非法 → 9（最低）。
    合并去重时「保最高层级」= 取 rank 最小者。"""
    if not isinstance(tier, str):
        return 9
    m = re.match(r"[sS]([1-4])", tier.strip())
    return int(m.group(1)) if m else 9


def _track_norm_url(url: Any) -> str:
    """URL 去重规范化：去空白、去尾部 '/'、整体小写。仅用于去重键，不改写落盘的原 url。"""
    if not isinstance(url, str):
        return ""
    return url.strip().rstrip("/").lower()


def merge_track_reports(labeled_reports: list[tuple[str, str]]) -> str:
    """PAR-2（纯）：把各轨报告拼接到 '# Track N: <angle>' 一级标题之下。

    labeled_reports = [(angle_title, report_text), ...]（保持轨顺序）。空文本的轨跳过。
    Track 编号按传入顺序从 1 起（跳过的轨不占号，编号连续）。
    """
    parts: list[str] = []
    n = 0
    for angle, text in labeled_reports:
        body = (text or "").strip()
        if not body:
            continue
        n += 1
        parts.append(f"# Track {n}: {angle}\n\n{body}")
    return "\n\n".join(parts)


# W9-7: 多轨卷宗的一次有界 LLM 整理 —— merge_track_reports 的纯拼接会把 3 份执行摘要、
# 互相矛盾的头条数字原封不动地交付（诊断实测：同一轨内 2030 基线 $1.45-1.55T vs $1.7-1.9T）。
# 这里补一个「合并开篇 + 跨轨分歧表 + H1 降级」的编辑整理；degrade-safe：任何失败回退纯拼接。

_TRACK_H1_RE = re.compile(r"(?m)^# Track (\d+): (.+?)\s*$")


def _track_title_en(title: str) -> str:
    """W9-7（纯）：从 '基线证据扫描 (Base Evidence Sweep)' 提取英文标题；无括号英文时原样返回。"""
    m = re.search(r"[（(]([A-Za-z][^)）]*)[)）]\s*$", str(title or ""))
    return (m.group(1).strip() if m else str(title or "").strip())


def demote_track_headings(md: str) -> str:
    """W9-7（纯）：把 '# Track N: <title>' H1 降级为 '## Part N — <english title>' H2。

    合并卷宗只应有一个文档级叙事层级；'# Track N' H1 把 3 份独立报告的骨架原样漏给读者。
    仅改写 merge_track_reports 生成的行（正则锚定行首），正文其余内容逐字节不动。
    """
    def _sub(m: "re.Match[str]") -> str:
        return f"## Part {m.group(1)} — {_track_title_en(m.group(2))}"
    return _TRACK_H1_RE.sub(_sub, md or "")


def extract_exec_summary(report_md: str, max_chars: int = 4000) -> str:
    """W9-7（纯）：提取单轨报告的 Executive Summary 小节正文（到下一个 H1/H2 为止）。

    找不到该标题时回退取开头 ``max_chars``（多数轨的头部即摘要性内容）。"""
    text = report_md or ""
    m = re.search(r"(?mi)^#{1,3}\s+.*executive summary.*$", text)
    if m:
        start = m.end()
        nxt = re.search(r"(?m)^#{1,2}\s+", text[start:])
        seg = text[start:start + nxt.start()] if nxt else text[start:]
        seg = seg.strip()
        if seg:
            return seg[:max_chars]
    return text.strip()[:max_chars]


def reconcile_track_reports(merged_report: str,
                            labeled_reports: list[tuple[str, str]],
                            llm: Any = None, *,
                            max_prompt_chars: int = 30000) -> tuple[str, dict]:
    """W9-7：合并卷宗的一次有界 LLM 编辑整理，返回 (整理后的卷宗, 审计 dict)。

    输入只喂各轨标题 + 执行摘要（总量 cap ``max_prompt_chars``≈30K 字符，绝不喂全文），
    产出一个「合并执行摘要 + 跨轨分歧（Cross-track disagreements）」开篇；随后确定性地把
    '# Track N' H1 降级为 '## Part N — <english title>' H2 并把开篇前置。degrade-safe：
    LLM 失败/输出可疑（过短/错误串）→ 返回 (merged_report 原文, audit{applied:False})，
    与今日纯拼接逐字节一致。
    """
    audit: dict[str, Any] = {"applied": False, "reason": ""}
    try:
        tracks = [(t, r) for t, r in (labeled_reports or []) if (r or "").strip()]
        if len(tracks) < 2:
            audit["reason"] = "tracks<2"
            return merged_report, audit
        per = max(2000, (max_prompt_chars - 2000) // len(tracks))
        blocks = []
        for i, (title, rep) in enumerate(tracks, 1):
            blocks.append(f"### Track {i}: {title}\n{extract_exec_summary(rep, per)}")
        payload = "\n\n".join(blocks)[:max_prompt_chars]
        prompt = (
            "你是一名研究卷宗的终审编辑。下面是同一预测问题的多条并行研究轨各自的执行摘要"
            "（轨 1 = 基线证据扫描；其余轨为角度特化视角）。请产出合并卷宗的统一开篇，"
            "要求：\n"
            "1. 以 '## Executive Summary' 开头，把各轨执行摘要合并为一份连贯的摘要"
            "（去重、保留各轨独有的关键结论，不要逐轨罗列）。\n"
            "2. 紧随其后输出 '### Cross-track disagreements' 小节：用列表逐条点名各轨"
            "互相冲突的头条数字/结论（指标、各轨口径、差异），没有实质冲突就写"
            "「未发现实质性跨轨分歧」。\n"
            "3. 只输出上述 markdown，不要前言/解释；绝不提及研究 pass、working notes、"
            "轨道机制等管线内部词汇；语言与输入摘要的主要语言一致。\n\n"
            f"{payload}"
        )
        if llm is None:
            from ..utils.llm_client import LLMClient
            llm = LLMClient()
        out = llm.chat([{"role": "user", "content": prompt}],
                       temperature=0.2, max_tokens=4000)
        out = (out or "").strip()
        if len(out) < 200 or any(mk in out for mk in _LLM_ERROR_MARKERS):
            audit["reason"] = f"llm output rejected (len={len(out)})"
            return merged_report, audit
        body = demote_track_headings(merged_report)
        audit.update({"applied": True, "opening_chars": len(out), "tracks": len(tracks)})
        return out.rstrip() + "\n\n" + body.lstrip(), audit
    except Exception as e:  # noqa: BLE001 — 整理是增强，失败必回退纯拼接
        audit["reason"] = f"{type(e).__name__}: {e}"
        return merged_report, audit


def merge_sources_union(track_sources: list[Any]) -> list[dict]:
    """PAR-2（纯）：跨轨合并来源，按规范化 URL 去重，冲突时保最高层级（S1>S2>…）。

    非 dict / 缺 url 的行原样保留（无法去重）。相同 URL：保留首见行，把更高层级写回、
    并用任一轨的非空字段回填缺失字段（保守合并，绝不丢信息）。保持首见顺序（确定性）。
    """
    out: list[dict] = []
    index: dict[str, int] = {}
    for sources in track_sources:
        if not isinstance(sources, list):
            continue
        for s in sources:
            if not isinstance(s, dict):
                continue
            key = _track_norm_url(s.get("url"))
            if not key:
                out.append(dict(s))
                continue
            if key not in index:
                index[key] = len(out)
                out.append(dict(s))
            else:
                existing = out[index[key]]
                if _track_tier_rank(s.get("tier")) < _track_tier_rank(existing.get("tier")):
                    existing["tier"] = s.get("tier")
                for k, v in s.items():
                    if v and not existing.get(k):
                        existing[k] = v
    return out


def merge_list_dedup(track_lists: list[Any]) -> list:
    """PAR-2（纯）：跨轨拼接数组并按规范 JSON 恒等去重（quantitative/contested/timeline 用）。
    保持首见顺序；不可序列化的元素回退 repr 作键。"""
    out: list = []
    seen: set[str] = set()
    for lst in track_lists:
        if not isinstance(lst, list):
            continue
        for item in lst:
            try:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                key = repr(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def merge_actors_objs(track_actor_objs: list[Any]) -> tuple[Optional[dict], dict]:
    """PAR-2：跨轨合并 actors.json 对象，再走既有 reconcile_cast 去重。

    先并集各轨的 actors[]/relationships[]/key_events[]，标量字段（central_question/
    as_of_date/situation_brief/hot_topics…）取首个非空轨；然后调用 reconcile_cast 对重复
    actor 做规范化合并并改写关系端点。返回 (merged_obj|None, audit)。全轨无 actors → (None, 空 audit)。
    """
    base: Optional[dict] = None
    all_actors: list = []
    all_rel: list = []
    all_events: list = []
    for obj in track_actor_objs:
        if not isinstance(obj, dict):
            continue
        if base is None:
            base = {k: v for k, v in obj.items()
                    if k not in ("actors", "relationships", "key_events")}
        all_actors.extend(obj.get("actors") or [])
        all_rel.extend(obj.get("relationships") or [])
        all_events.extend(obj.get("key_events") or [])
    if base is None:
        return None, {"merged": [], "n_before": 0, "n_after": 0}
    merged = dict(base)
    merged["actors"] = all_actors
    if all_rel:
        merged["relationships"] = all_rel
    if all_events:
        merged["key_events"] = all_events
    try:
        from ..utils.actors import reconcile_cast as _reconcile
        recon, audit = _reconcile(merged)
        return recon, audit
    except Exception:  # noqa: BLE001 — 去重失败回退未去重并集（union-dedup 兜底）
        return merged, {"merged": [], "n_before": len(all_actors), "n_after": len(all_actors)}


def merge_research_quality(track_metas: list[Any]) -> dict:
    """PAR-2（纯）：合并 research_quality = 各轨 score 的最小值（保守），附每轨明细。

    以 score 最小的轨的 research_quality 块为基底（其 components 内部自洽），加 per_track
    明细与 merged_from_tracks 计数。无任何轨带评分 → 仅返回 {per_track, merged_from_tracks}。
    """
    per_track: list[dict] = []
    min_rq: Optional[dict] = None
    min_score: Optional[float] = None
    for i, meta in enumerate(track_metas, start=1):
        rq = meta.get("research_quality") if isinstance(meta, dict) else None
        if not isinstance(rq, dict):
            per_track.append({"track": i, "research_quality": None})
            continue
        score = rq.get("score")
        per_track.append({"track": i, "score": score, "components": rq.get("components")})
        if isinstance(score, (int, float)):
            if min_score is None or score < min_score:
                min_score = score
                min_rq = rq
    merged = dict(min_rq) if isinstance(min_rq, dict) else {}
    merged["per_track"] = per_track
    merged["merged_from_tracks"] = len(track_metas)
    return merged


def _source_tier_histogram(sources: Any) -> dict[str, int]:
    """PAR-2（纯）：从（合并后的）sources 重算 {s1_count..s4_count,s_unknown}，键名与 bridge 一致。"""
    hist = {"s1_count": 0, "s2_count": 0, "s3_count": 0, "s4_count": 0, "s_unknown": 0}
    if not isinstance(sources, list):
        return hist
    for s in sources:
        r = _track_tier_rank(s.get("tier")) if isinstance(s, dict) else 9
        hist[f"s{r}_count" if 1 <= r <= 4 else "s_unknown"] += 1
    return hist


def pick_freshest_markets(track_markets: list[Any]) -> Optional[dict]:
    """PAR-2（纯）：跨轨取「最新」预测市场快照（按 as_of ISO 字符串字典序=时间序，取最大）。

    优先含非空 markets 的快照；全为空则回退任一 dict；全无 → None。
    """
    best: Optional[dict] = None
    best_key: str = ""
    for pm in track_markets:
        if not isinstance(pm, dict) or not pm.get("markets"):
            continue
        k = str(pm.get("as_of") or "")
        if best is None or k > best_key:
            best, best_key = pm, k
    if best is None:
        for pm in track_markets:
            if isinstance(pm, dict):
                return pm
    return best


def load_research_dossier_for_simulation(simulation_id: Optional[str]) -> dict[str, Any]:
    """T4.1: best-effort 找到含该 simulation_id 的管线 handoff，加载研究档案。

    供手动报告路径（api/report.py）复用：让手动生成/对话也能钉入背景档案。找不到对应
    管线 / handoff 时全部返回 None，ReportAgent 回退到冷图盲搜路径（与旧行为一致）。
    """
    out: dict[str, Any] = {
        "situation_brief": None, "actors": None, "sources": None, "research_report": None,
        "actor_dossier": None,  # 双轨 Track B：角色本体档案，供手动报告路径作背景上下文（缺失为 None）
    }
    if not simulation_id:
        return out
    try:
        for entry in PipelineManager.list_pipelines():
            pid = entry.get("pipeline_id")
            if not pid:
                continue
            data = PipelineManager.load(pid)
            if not data or data.get("simulation_id") != simulation_id:
                continue
            hd = data.get("handoff_dir") or PipelineManager.handoff_dir(pid)
            actors = _read_json(os.path.join(hd, "actors.json"))
            report = _read_text(os.path.join(hd, "research_report.md"))
            dossier = _read_text(os.path.join(hd, "actor_dossier.md"))
            out["actors"] = actors
            out["sources"] = _read_json(os.path.join(hd, "sources.json"))
            out["research_report"] = report or None
            out["actor_dossier"] = dossier or None
            out["situation_brief"] = situation_brief(actors) if actors else None
            break
    except Exception:  # best-effort enrichment must never break manual report generation
        pass
    return out


def preflight_pipeline(mode: str = "full", model: Optional[str] = None) -> list[str]:
    """启动管线前的快速体检：把会在几十分钟后才暴露的配置错误提前到 POST /run 时。

    只做廉价的本地检查（文件存在性 / PATH / 环境变量），不发任何网络请求。
    返回人类可读的错误列表；为空表示可以起飞。

    Args:
        mode: full / research_only。research_only 在研究完成后即返回，
              全程不碰 Zep 与报告/模拟 LLM，故跳过这两项检查。
    """
    import shutil

    errors: list[str] = []
    full_mode = mode != "research_only"

    # 1) 本地知识图谱后端（建图阶段硬依赖，仅 full 模式）。
    #    本地 Graphiti 无需任何 API Key；仅检查嵌入式后端是否可导入。
    if full_mode:
        import importlib.util

        backend = Config.GRAPH_BACKEND
        if backend in ('auto', 'falkordblite') and importlib.util.find_spec(
            'redislite.async_falkordb_client'
        ) is None:
            if backend == 'auto' and importlib.util.find_spec('kuzu') is not None:
                pass  # auto 会回退到 kuzu
            elif backend == 'auto' and Config.FALKORDB_HOST:
                pass  # auto 会使用外部 FalkorDB 服务
            else:
                errors.append(
                    "本地知识图谱后端未安装。运行 ./setup.sh，或手动安装 "
                    "'falkordblite'（嵌入式，推荐，Python>=3.12）或 'kuzu'。"
                )
        elif backend == 'kuzu' and importlib.util.find_spec('kuzu') is None:
            errors.append("GRAPH_BACKEND=kuzu 但未安装 kuzu。运行 ./setup.sh 或 pip install kuzu。")

    # 2) 报告/模拟阶段的 LLM 提供方（仅 full 模式）
    if full_mode:
        meta = Config.PROVIDER_META.get(Config.LLM_PROVIDER, {})
        if meta.get('needs_key') and not Config.LLM_API_KEY:
            errors.append(f"LLM_PROVIDER={Config.LLM_PROVIDER} 需要 LLM_API_KEY（写入 .env 或在设置菜单填写）")
        if Config.LLM_PROVIDER == 'claude-cli' and shutil.which('claude') is None:
            errors.append("LLM_PROVIDER=claude-cli 但未找到 `claude` CLI。安装 Claude Code（https://claude.com/claude-code）或在设置中切换提供方")
        if Config.LLM_PROVIDER == 'codex-cli' and shutil.which('codex') is None:
            errors.append("LLM_PROVIDER=codex-cli 但未找到 `codex` CLI。安装 Codex CLI 或在设置中切换提供方")

    # 3) DeerFlow 研究引擎（stage 1 硬依赖）
    script = os.path.join(Config.DEERFLOW_DIR, 'deerflow_research.py')
    if not os.path.isdir(Config.DEERFLOW_DIR) or not os.path.exists(script):
        errors.append(
            f"DeerFlow 研究引擎未就绪（{Config.DEERFLOW_DIR}）。"
            "在项目根目录运行 ./setup.sh 自动下载并配置（或设置 DEERFLOW_DIR 指向现有 checkout）"
        )

    # 4) 研究模型的凭据（T5.5：per-run model 覆盖时校验覆盖模型的 Key，而非 Config 默认）
    df_model = (model or Config.DEERFLOW_MODEL or 'claude').lower()
    # T6.4: 单一真源 Config.DEERFLOW_KEY_ENV（与 validate()/doctor.sh 共用，避免三处漂移）
    _df_key_env = Config.DEERFLOW_KEY_ENV
    if df_model in _df_key_env and not os.environ.get(_df_key_env[df_model], '').strip():
        errors.append(f"DEERFLOW_MODEL={df_model} 需要环境变量 {_df_key_env[df_model]}（写入 .env）")
    elif df_model == 'claude':
        has_oauth = (
            os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip()
            or os.environ.get('ANTHROPIC_AUTH_TOKEN', '').strip()
            or os.path.exists(os.path.expanduser('~/.claude/.credentials.json'))
            or shutil.which('claude') is not None  # CLI 在则凭据多半在 Keychain
        )
        if not has_oauth:
            errors.append("DEERFLOW_MODEL=claude 需要 Claude Code 登录凭据：安装 `claude` CLI 并运行一次 `claude` 完成登录")
    elif df_model == 'codex':
        if not os.path.exists(os.path.expanduser('~/.codex/auth.json')) and shutil.which('codex') is None:
            errors.append("DEERFLOW_MODEL=codex 需要 Codex 登录凭据（~/.codex/auth.json）：安装 `codex` CLI 并登录")

    return errors


def _actors_to_context(actors: Optional[dict]) -> Optional[str]:
    """把 actors.json 压成一段给 OntologyGenerator 的 additional_context，
    引导本体偏向真实命名实体。"""
    if not isinstance(actors, dict):
        return None
    rows = actors.get("actors") or []
    if not rows:
        return None
    lines = ["根据深度研究，本事件涉及以下真实命名实体（请让本体覆盖这些类型的角色）："]
    for a in rows[:25]:
        if not isinstance(a, dict):
            continue
        name = a.get("name", "?")
        typ = a.get("type", "")
        role = a.get("role", "")
        stance = a.get("stance", "")
        lines.append(f"- {name}（{typ}）：{role} 立场：{stance}".strip())
    topics = actors.get("hot_topics") or []
    if topics:
        lines.append("热点议题：" + "、".join(str(t) for t in topics[:10]))

    # T2.8: 把局势简报 + 调研确认的角色关系喂给本体生成，让 edge_types 覆盖真实关系类型，
    # source_targets 连接这些角色所属的实体类型（而非凭报告行文凭空发明边类型）。
    sb = situation_brief_block(actors)
    if sb:
        lines.append("")
        lines.append(sb)
    rels = extract_relationship_rows(actors)
    if rels:
        lines.append("")
        lines.append(
            "以下角色间关系均为深度研究实证——你的 edge_types 应覆盖这些关系类型，"
            "source_targets 应连接这些角色所属的实体类型："
        )
        for r in rels[:30]:
            typ = str(r.get("type", "")).upper()
            label = REL_LABEL.get(typ, typ or "关联")
            lines.append(f"- {r.get('source')} --[{label}/{typ}]--> {r.get('target')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# I-8-1: 每次运行的可复现性清单 run.json
# ---------------------------------------------------------------------------
# 因为 LLM 提供方可在运行时经 Config.apply_provider 热切换，事后查看 .env 无法可靠还原
# 某份报告到底是「哪个模型、几个 agent、什么研究深度」生成的。run.json 把完整解析后的
# 运行配置 + 环境指纹（已脱敏）快照下来，使每份预测可审计、可复现、可 A/B 对比。
# 默认开（成本字段几乎零开销）；包版本冻结（uv pip freeze，较慢）默认关。


def _repo_git_sha() -> Optional[str]:
    """当前仓库的 git SHA（best-effort，非 git checkout / 无 git 时返回 None）。"""
    try:
        # config.py 在 backend/app/，仓库根 = 上溯三级
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        r = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = (r.stdout or "").strip()
        return sha if r.returncode == 0 and sha else None
    except (OSError, subprocess.SubprocessError):
        return None


def _deerflow_ref() -> Optional[str]:
    """deer-flow checkout 的 commit（用于复现研究阶段）；vendored / 无 git 时降级。"""
    try:
        deerflow_dir = getattr(Config, "DEERFLOW_DIR", "") or ""
        if not deerflow_dir or not os.path.isdir(deerflow_dir):
            return None
        if not os.path.isdir(os.path.join(deerflow_dir, ".git")):
            return "vendored"  # 子目录而非独立 git checkout（如 shallow vendoring）
        r = subprocess.run(
            ["git", "-C", deerflow_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = (r.stdout or "").strip()
        return sha if r.returncode == 0 and sha else "vendored"
    except (OSError, subprocess.SubprocessError):
        return None


def _capture_key_packages() -> Optional[dict[str, Any]]:
    """可选包版本冻结（MANIFEST_CAPTURE_VERSIONS=true 时）。

    优先用 importlib.metadata 取几个关键包的版本（廉价、无子进程）；不引入新依赖，
    失败静默降级到 None。
    """
    try:
        from importlib import metadata as _md
    except Exception:
        return None
    out: dict[str, Any] = {}
    for pkg in ("graphiti-core", "sentence-transformers", "openai", "flask",
                "redislite", "kuzu", "falkordb"):
        try:
            out[pkg] = _md.version(pkg)
        except Exception:
            continue
    return out or None


def _build_run_manifest(state: "PipelineState") -> dict[str, Any]:
    """构造 run.json 的内容快照（secrets 已脱敏）。

    resolved 段在管线各阶段进入时由 PipelineOrchestrator._update_manifest 增量填充；
    本函数生成首版骨架 + 不随阶段变化的环境/图谱指纹。
    """
    opts = state.options or {}
    depth = (opts.get("depth") or getattr(Config, "DEERFLOW_RESEARCH_DEPTH", "standard"))
    research_model = opts.get("research_model") or getattr(Config, "DEERFLOW_MODEL", "claude")
    # CONF-1：快照记录「生效」预算（含双轨/子代理/扇出的 ×1.5 放大），与看门狗实际用值一致；
    # 老 Config（无该 classmethod）时回退原始档位 dict（degrade-safe）。
    try:
        research_timeout = Config.deerflow_depth_budget(depth)
    except (AttributeError, TypeError):  # noqa: BLE001 — 快照绝不阻塞管线启动
        depth_budgets = getattr(Config, "DEERFLOW_DEPTH_BUDGETS", {}) or {}
        research_timeout = depth_budgets.get(
            str(depth).lower(), getattr(Config, "DEERFLOW_RESEARCH_TIMEOUT", 0)
        )
    max_rounds = opts.get("max_rounds") or (getattr(Config, "OASIS_DEFAULT_MAX_ROUNDS", 0) or None)

    manifest: dict[str, Any] = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "pipeline_id": state.pipeline_id,
        "mode": state.mode,
        "created_at": state.created_at,
        "updated_at": _utcnow(),
        "repo_git_sha": _repo_git_sha(),
        "deerflow_ref": _deerflow_ref(),
        "resolved": {
            "research": {
                "model": research_model,
                "depth": str(depth).lower() if depth else None,
                "timeout_s": research_timeout,
                "language": opts.get("research_language"),
            },
            # provider 在每阶段边界由 _update_manifest 钉入实际值（可热切换）
            "ontology": {"provider": None, "model_name": None},
            "graph": {"provider": None, "model_name": None},
            "report": {"provider": None, "model_name": None},
            "simulation": {
                "max_agents": getattr(Config, "OASIS_MAX_AGENTS", None),
                "max_rounds": int(max_rounds) if max_rounds else None,
                "total_rounds": None,  # 真实总轮数运行时填
                "recsys_wired": bool(getattr(Config, "SIM_WIRE_RECSYS", False)),
                "sim_graph_feedback": bool(getattr(Config, "SIM_GRAPH_FEEDBACK", True)),
            },
        },
        "graph": {
            "backend": getattr(Config, "GRAPH_BACKEND", None),
            "embed_model": getattr(Config, "GRAPHITI_EMBED_MODEL", None),
            "embed_dim": getattr(Config, "GRAPHITI_EMBED_DIM", None),
            "reranker": getattr(Config, "GRAPHITI_RERANKER", None),
        },
        "env_fingerprint": {
            "python": _python_version_string(),
        },
        # 字段名刻意避开 redact_secrets 的 secret-key 正则（含 "key"/"secret"/"token" 的键名
        # 会被整值替换为 ***REDACTED***）：用 redacted_flag / package_versions 而非
        # secrets_redacted / key_packages，避免观测字段被误脱敏。
        "redacted_flag": True,
    }
    if bool(getattr(Config, "MANIFEST_CAPTURE_VERSIONS", False)):
        pkgs = _capture_key_packages()
        if pkgs:
            manifest["env_fingerprint"]["package_versions"] = pkgs
    return manifest


def _python_version_string() -> str:
    import sys
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _current_provider_pair() -> dict[str, Optional[str]]:
    """当前 Config 解析出的报告/模拟阶段提供方 + 模型名（随热切换变化）。"""
    return {
        "provider": getattr(Config, "LLM_PROVIDER", None),
        "model_name": getattr(Config, "LLM_MODEL_NAME", None),
    }


# ---------------------------------------------------------------------------
# I-4-3: 产物完整性（流式 sha256 + 轻量 schema 探针）
# ---------------------------------------------------------------------------


def _sha256_file(path: str, chunk_size: int = 1 << 20) -> Optional[str]:
    """流式计算文件 sha256（1MiB 分块，避免把大研究报告/图谱 dump 整入内存）。

    文件不存在 / 读失败 → None（视为「无法核验」，由调用方决定保守重建还是放行）。
    """
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def _probe_artifact_schema(name: str, path: str) -> bool:
    """I-4-3: 对已知产物做廉价的「结构是否像样」探针（best-effort，绝不抛出）。

    目的不是严格校验，而是抓住「半写/截断/被错误文本覆盖」这类粗暴损坏：
      - actors.json  → dict 且含 list 形 'actors'
      - ontology.json→ dict 且含非空 'entity_types'
      - communities.json → list
      - *.md / *.csv / 其它 → 仅看非空（交由各自阶段的语义守卫，如 research 的 400 字门）
    未知产物 / 读失败 → True（不因探针缺位误杀一份本可复用的产物）。
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= 0:
            return False
        base = os.path.basename(path)
        if base.endswith(".json"):
            data = _read_json(path)
            if data is None:
                return False
            if base == "actors.json":
                return isinstance(data, dict) and isinstance(data.get("actors"), list)
            if base == "ontology.json":
                return isinstance(data, dict) and bool(data.get("entity_types"))
            if base == "communities.json":
                return isinstance(data, list)
            # 其它 JSON：能解析即视为通过（dict/list 皆可）。
            return isinstance(data, (dict, list))
        # 非 JSON（md/csv）：非空即可（语义门交给阶段自身）。
        return os.path.getsize(path) > 0
    except Exception:  # noqa: BLE001 — 探针永不拦截一次合法的新跑
        return True


def _manifest_entry_for(name: str, path: str, stage: str) -> Optional[dict[str, Any]]:
    """I-4-3: 为单个产物构造清单条目（路径/sha256/字节/产出阶段/产出时间/schema_ok）。

    文件缺失返回 None（不登记不存在的产物）。
    """
    if not path or not os.path.exists(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 0:
        return None
    return {
        "path": path,
        "sha256": _sha256_file(path),
        "bytes": size,
        "produced_by_stage": stage,
        "produced_at": _utcnow(),
        "schema_ok": _probe_artifact_schema(name, path),
    }


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """串联 research → ontology → graph → prepare → run → report。"""

    _threads: dict[str, threading.Thread] = {}
    _cancel_events: dict[str, threading.Event] = {}
    _cleanup_registered: bool = False
    # 串行化 resume/cancel 的"读状态→判定→写状态/起线程"临界区：没有它，两个并发
    # POST /resume 都能在对方落盘 running 之前通过状态检查，对同一管线起两条 _run
    # 线程（双倍烧额度 + 状态互相覆盖）。
    _lifecycle_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        # I-4-6: 运行中临时产物扫描的「上次扫描壁钟」按阶段节流戳（仅本实例/本次运行有效）。
        self._partial_scan_at: dict[str, float] = {}
        # W9-3: run_telemetry.json 增量落盘状态（attempt 起点由 _init_telemetry_flush 填充）。
        self._tel_lock = threading.Lock()
        self._tel_path: Optional[str] = None
        self._tel_prev: Optional[dict] = None
        self._tel_prev_cum: Optional[dict] = None
        self._tel_last_flush_calls: int = 0

    # -- W9-3: run 遥测增量落盘 --------------------------------------------
    # 两条失败跑的教训：LLMMeter 是进程内存累加器，重启即清零；run_telemetry.json 只在
    # _run 的 finally 落一次盘——被 SIGTERM 的跑整跑 $20+ 的 token/成本账直接归零。
    # 这里把落盘改成增量：attempt 起点一次性捕获上一 attempt 的账（合并基底），此后每次
    # 阶段转换 + 每 PIPELINE_TELEMETRY_FLUSH_EVERY_CALLS 次新 LLM 调用（在心跳/进度回调上
    # 检查）都把当前快照原子落盘。与 LLMMeter.write_run_telemetry 的关键差异：合并基底
    # 固定在 attempt 起点，反复 flush 不会把本 attempt 的中间快照误当「上一 attempt」重复
    # 累进 cumulative_total。

    def _init_telemetry_flush(self, state: "PipelineState") -> None:
        """attempt 起点：定位 run_telemetry.json 并捕获上一 attempt 的账作为合并基底。"""
        self._tel_path = os.path.join(PipelineManager._dir(state.pipeline_id), "run_telemetry.json")
        self._tel_prev = None
        self._tel_prev_cum = None
        self._tel_last_flush_calls = 0
        try:
            prev = _read_json(self._tel_path)
            if isinstance(prev, dict) and (prev.get("total") or {}).get("calls"):
                self._tel_prev = {
                    "total": prev.get("total"),
                    "report_id": prev.get("report_id"),
                    "status": prev.get("status"),
                }
                self._tel_prev_cum = prev.get("cumulative_total") or prev.get("total") or {}
        except Exception:  # noqa: BLE001 — 基底捕获失败按首写处理
            pass

    def _flush_run_telemetry(self, state: "PipelineState", *, final: bool = False,
                             extra: Optional[dict] = None) -> None:
        """把 LLMMeter 快照原子落盘 run_telemetry.json（增量 in_flight / 终态 final）。

        degrade-safe：任何失败仅 debug 日志，绝不影响管线。未经 _init_telemetry_flush
        初始化（_tel_path 为 None）时为 no-op。
        """
        tpath = self._tel_path
        if not tpath:
            return
        from ..utils.telemetry import LLMMeter
        from ..utils.atomic import write_json_atomic
        with self._tel_lock:
            try:
                data = LLMMeter.snapshot(state.pipeline_id)
                data.update({
                    "pipeline_id": state.pipeline_id,
                    "status": state.status,
                    "report_id": state.report_id,
                })
                if extra:
                    data.update(extra)
                if not final:
                    data["in_flight"] = True  # 运行中快照标记（终版落盘时消失）
                if self._tel_prev:
                    data["previous_attempt"] = self._tel_prev
                    base = self._tel_prev_cum or {}
                    cur = data.get("total") or {}
                    cum: dict[str, Any] = {}
                    for k in ("calls", "cached", "prompt_tokens", "completion_tokens",
                              "total_tokens", "latency_ms", "cost_usd"):
                        try:
                            cum[k] = round((base.get(k) or 0) + (cur.get(k) or 0), 6)
                        except TypeError:
                            continue
                    data["cumulative_total"] = cum
                write_json_atomic(tpath, data, fsync=final)
                self._tel_last_flush_calls = int((data.get("total") or {}).get("calls") or 0)
            except Exception as _fe:  # noqa: BLE001 — 遥测落盘失败不得影响管线
                logger.debug("[%s] run_telemetry 增量落盘失败（忽略）: %s", state.pipeline_id, _fe)

    def _maybe_flush_run_telemetry(self, state: "PipelineState") -> None:
        """按 LLM 调用增量节流 flush：距上次落盘新增 ≥N 次调用才写。N<=0 关闭增量 flush。"""
        if not self._tel_path:
            return
        try:
            n = int(getattr(Config, "PIPELINE_TELEMETRY_FLUSH_EVERY_CALLS", 20) or 0)
        except (TypeError, ValueError):
            n = 20
        if n <= 0:
            return
        try:
            from ..utils.telemetry import LLMMeter
            calls = int((LLMMeter.snapshot(state.pipeline_id).get("total") or {}).get("calls") or 0)
        except Exception:  # noqa: BLE001
            return
        if calls - self._tel_last_flush_calls >= n:
            self._flush_run_telemetry(state)

    # -- 生命周期：启动回收 + 关闭清理 ------------------------------------

    @classmethod
    def reconcile_orphans(cls) -> None:
        """后端启动时回收孤儿管线。

        硬杀 / 崩溃 / 重启会跳过 ``_run`` 的 except 块，使 pipeline_state.json 永远停在
        ``running``；前端 ``poll()`` 只在 completed/failed 时停止，于是无限空转。进程刚启动时
        ``_threads`` 必为空，故任何持久化为 running 的管线都是上一进程遗留的孤儿 → 标记 failed。

        I-4-1: 旧逻辑「不在本进程 _threads 即孤儿」只在「单进程、_threads 即真相」下成立。多
        worker / gunicorn / 重启竞态下，它会误杀另一进程里真正在飞的管线，或让两个 worker 都
        以为自己拥有同一条。开启 PIPELINE_HEARTBEAT_ENABLED（默认）时改为基于证据的存活判定：
        仅当 (owner 进程已不在 / boot_id 不是本进程 / owner 未知) 且 心跳过期 才回收；owner 进程
        仍活且心跳新鲜的「慢但活」管线一律保留。关闭该开关时回退到旧的 _threads 判定。
        """
        try:
            from ..models.task import TaskManager
            # ORCH-2: reconcile 在 create_app() 里、**绑定端口之前**运行。第二个后端在第一个仍
            # 服务 :5001 时启动，会先破坏性地把活管线回收成 failed（并杀掉其研究子进程），然后才
            # 死于 'Address already in use'——一个注定失败的重复进程踩死了活进程拥有的状态。
            # 端口已被占用 = 极可能另一后端在跑 → 整体跳过孤儿回收（由那个活进程自己管理）。
            if bool(getattr(Config, "PIPELINE_RECLAIM_PORT_PROBE", True)):
                import socket
                try:
                    _port = int(os.environ.get("FLASK_PORT", "5001") or "5001")
                except ValueError:
                    _port = 5001
                try:
                    with socket.create_connection(("127.0.0.1", _port), timeout=0.2):
                        logger.warning(
                            "端口 %d 已被占用，疑似另一后端在跑——跳过孤儿管线回收", _port)
                        return
                except OSError:
                    pass  # 端口空闲 → 本进程将是唯一后端，正常回收
            task_manager = TaskManager()
            hb_enabled = bool(getattr(Config, "PIPELINE_HEARTBEAT_ENABLED", True))
            stale_s = float(getattr(Config, "PIPELINE_HEARTBEAT_STALE_S", 120) or 120)
            for p in PipelineManager.list_pipelines():
                if p.get("status") != "running":
                    continue
                pipeline_id = p.get("pipeline_id")
                if not pipeline_id or pipeline_id in cls._threads:
                    continue
                # I-4-1: 心跳模式下，先判定该 running 管线是否仍可能在别处存活。
                if hb_enabled and not cls._orphan_is_dead(pipeline_id, stale_s):
                    logger.info(
                        "[%s] 持久化为 running 但有存活证据（owner 进程活/心跳新鲜），暂不回收",
                        pipeline_id,
                    )
                    continue
                # W9-1：打捞——主报告已完整落盘（report 阶段 completed + full_report.md +
                # forecast.json 在盘）的孤儿不再整线判死。中断只发生在报告之后的多种子集成/
                # 收尾窗口（pipe_a8986bffd918 / pipe_0f2bee7bd649 两条 $10+ 的跑正是这样被
                # 误判 failed 的）→ 落成 completed(degraded)，集成按已跳过处理。
                if bool(getattr(Config, "PIPELINE_SALVAGE_COMPLETED_ORPHANS", True)):
                    _data0 = PipelineManager.load(pipeline_id) or {}
                    _arts = cls._orphan_completed_report(_data0)
                    if _arts is not None:
                        _note = ("后端在多种子集成/收尾期间被中断；主报告已完整落盘，"
                                 "管线按 completed(degraded) 打捞恢复，多种子集成已跳过。")
                        if PipelineManager.mark_salvaged_completed(pipeline_id, _note):
                            logger.warning(
                                "[%s] 启动时打捞孤儿管线 → completed(degraded)"
                                "（报告 %s 完整在盘，集成跳过）",
                                pipeline_id, _arts.get("report_id"),
                            )
                            # 孤儿研究子进程仍要清理（打捞≠允许旧进程继续烧额度）。
                            cls._kill_orphan_research(pipeline_id, _data0.get("research_pid"))
                            for _tpid in (_data0.get("options") or {}).get("research_track_pids") or []:
                                cls._kill_orphan_research(pipeline_id, _tpid)
                            _tid = _data0.get("task_id")
                            if _tid:
                                try:
                                    task_manager.complete_task(_tid, result={
                                        "pipeline_id": pipeline_id,
                                        "report_id": _arts.get("report_id"),
                                        "salvaged": True,
                                    })
                                except Exception:  # noqa: BLE001
                                    pass
                            continue
                msg = "后端在运行中被中断（进程重启），该管线已标记为失败。"
                if PipelineManager.mark_failed(pipeline_id, msg):
                    logger.warning(f"[{pipeline_id}] 启动时回收孤儿管线 → failed")
                    data = PipelineManager.load(pipeline_id) or {}
                    # 杀掉上一进程遗留、仍在烧额度的孤儿研究子进程（按持久化的 PID）。
                    cls._kill_orphan_research(pipeline_id, data.get("research_pid"))
                    # PAR-2：并行研究轨会派生多个子进程，逐一清理（_kill_orphan_research 内部
                    # 会校验命令行含本 pipeline_id + deerflow_research.py，防 PID 复用误杀）。
                    for _tpid in (data.get("options") or {}).get("research_track_pids") or []:
                        cls._kill_orphan_research(pipeline_id, _tpid)
                    tid = data.get("task_id")
                    if tid:
                        try:
                            task_manager.fail_task(tid, msg)
                        except Exception:
                            pass
            # W9-10：启动时顺带做一次图谱 GC（保留最近 GRAPH_RETAIN_COUNT 张）。
            cls._gc_stale_graphs()
        except Exception as e:  # noqa: BLE001 — 回收失败不应阻断启动
            logger.error(f"回收孤儿管线失败: {e}", exc_info=True)

    @staticmethod
    def _orphan_completed_report(data: dict, report_dir: Optional[str] = None) -> Optional[dict]:
        """W9-1（可单测）：孤儿管线是否已有「完整报告交付物」可打捞。

        判据（全部满足才打捞，宁可漏捞不可把真坏的跑洗白）：
          - state.report_id 存在；
          - report 阶段 status == 'completed'（报告链自己宣布过成功）；
          - 报告目录下 full_report.md 非空 且 forecast.json 可解析为非空 dict。
        满足 → 返回 {'report_id', 'report_dir'}；任一不满足 → None（沿用判死路径）。
        ``report_dir`` 缺省时经 ReportManager 解析（测试可显式传入临时目录）。
        """
        if not isinstance(data, dict):
            return None
        report_id = data.get("report_id")
        if not report_id:
            return None
        st = (data.get("stages") or {}).get(STAGE_REPORT)
        if not (isinstance(st, dict) and st.get("status") == "completed"):
            return None
        if report_dir is None:
            try:
                report_dir = ReportManager._get_report_folder(report_id)
            except Exception:  # noqa: BLE001 — 解析不了报告目录 → 无从验证 → 不打捞
                return None
        try:
            fr = os.path.join(report_dir, "full_report.md")
            if not (os.path.isfile(fr) and os.path.getsize(fr) > 0):
                return None
        except OSError:
            return None
        fc = _read_json(os.path.join(report_dir, "forecast.json"))
        if not (isinstance(fc, dict) and fc):
            return None
        return {"report_id": report_id, "report_dir": report_dir}

    @staticmethod
    def _gc_stale_graphs() -> None:
        """W9-10: best-effort 图谱 GC——保留最近 GRAPH_RETAIN_COUNT 张图，回收更老的存储。

        实现体在 graph_builder.gc_stale_graphs（kg-backend 工作流并行落地）；模块/函数尚未
        部署（ImportError/AttributeError）或 retain<=0 时静默跳过，任何失败绝不阻断调用方
        （启动回收 / 管线删除）。
        """
        try:
            retain = int(getattr(Config, "GRAPH_RETAIN_COUNT", 5) or 0)
        except (TypeError, ValueError):
            retain = 5
        if retain <= 0:
            return
        try:
            from . import graph_builder as _gb
            # 兼容两种落地形态：模块级函数 gc_stale_graphs(retain=)，或
            # GraphBuilderService 实例方法（kg-backend 实际落地的形态）。
            _fn = getattr(_gb, "gc_stale_graphs", None)
            if _fn is None:
                _fn = getattr(_gb.GraphBuilderService(api_key=Config.ZEP_API_KEY),
                              "gc_stale_graphs")
            _res = _fn(retain=retain)
            if isinstance(_res, dict) and _res:
                logger.info(
                    "图谱 GC 完成（retain=%d）: total=%s kept=%d deleted=%s skipped=%s",
                    retain, _res.get("total"), len(_res.get("kept") or []),
                    _res.get("deleted"), _res.get("skipped_reason"))
            elif _res:
                logger.info("图谱 GC 完成（retain=%d）: %s", retain, _res)
        except (ImportError, AttributeError):
            logger.debug("graph_builder.gc_stale_graphs 未部署，跳过图谱 GC")
        except Exception as _gc_err:  # noqa: BLE001 — GC 纯回收，失败无声
            logger.warning("图谱 GC 失败（忽略）: %s", _gc_err)

    @classmethod
    def _orphan_is_dead(cls, pipeline_id: str, stale_s: float) -> bool:
        """I-4-1: 基于证据判定一个「持久化为 running 但不在本进程 _threads」的管线是否真的死了。

        判定（保守，宁可漏杀不可误杀活的）：
          - owner_boot_id == 本进程 _BOOT_ID 但又不在 _threads → 本进程自己的残留 → 视为死。
          - owner_pid 存在且进程仍存活（且非本进程的 boot）→ 可能是别的 worker 在跑 → 视为活
            （除非心跳已过期到 stale_s，说明那进程虽在但 hung / pid 复用 → 回收）。
          - owner_pid 不存在 / 已退出 / 字段缺失（老状态文件无 owner 信息）→ 心跳过期或缺失即视为死。
        """
        data = PipelineManager.load(pipeline_id)
        if not data or PipelineManager.INCOMPATIBLE_KEY in data:
            # 读不出 / schema 不兼容：无证据可依，沿用旧行为（视为死，交给 mark_failed）。
            return True
        owner_boot = data.get("owner_boot_id")
        owner_pid = data.get("owner_pid")
        hb_age = _age_seconds(data.get("heartbeat_at"))
        fresh = hb_age is not None and hb_age <= stale_s

        # 本进程自己写过 owner 但已不在 _threads → 线程确已结束的残留，回收。
        if owner_boot == _BOOT_ID:
            return True
        # owner 进程仍存活（属于别的后端进程/worker）：
        if _pid_alive(owner_pid):
            # 心跳仍新鲜 → 真在跑，保留；心跳过期 → 那进程 hung 或 pid 被复用 → 回收。
            return not fresh
        # owner 进程不存在 / 字段缺失：心跳新鲜（极少见，刚崩溃）也给一个宽限，否则回收。
        # ORCH-2: owner/heartbeat 双缺失（老状态文件、或手工修复过的状态）此前零宽限、立即判死。
        # 改为退回 updated_at 给同一个心跳窗口的宽限，避免刚手工编辑/刚崩溃的状态被秒杀。
        if hb_age is None:
            up_age = _age_seconds(data.get("updated_at"))
            return not (up_age is not None and up_age <= stale_s)
        return not fresh

    @staticmethod
    def _kill_orphan_research(pipeline_id: str, pid) -> None:
        """杀掉上一后端进程遗留的研究子进程组（按持久化 PID，谨慎校验防 PID 复用误杀）。"""
        if not pid:
            return
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return
        try:
            # PID 可能已被无关进程复用：先确认命令行确实是 deerflow_research.py
            check = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5,
            )
            cmdline = (check.stdout or "").strip()
            # 同时要求命令行含本管线的 handoff 路径（--out-dir .../<pipeline_id>/handoff 中已带
            # pipeline_id），避免 PID 复用时误杀同名脚本的另一条管线（EXECPLAN2 F-1-8）。
            if (check.returncode != 0
                    or "deerflow_research.py" not in cmdline
                    or pipeline_id not in cmdline):
                return  # 进程已退出，或 PID 已被复用/属于别的管线 → 不动
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            logger.warning(f"[{pipeline_id}] 已终止孤儿研究子进程组 pid={pid}")
        except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
            pass

    @classmethod
    def register_cleanup(cls) -> None:
        """注册后端关闭清理：终止在飞的 DeerFlow 研究子进程组。

        与 ``SimulationRunner.register_cleanup`` 同构，并链式调用此前已安装的信号处理器
        （通常是 SimulationRunner 的），因此两套清理在收到 SIGINT/SIGTERM/SIGHUP 时都会执行；
        ``atexit`` 覆盖正常退出 / ``sys.exit()``。
        """
        if cls._cleanup_registered:
            return

        # Flask debug 模式下只在 reloader 子进程（真正跑应用的进程）注册（与 SimulationRunner 一致）
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        if is_debug_mode and not is_reloader_process:
            cls._cleanup_registered = True
            return

        cls._cleanup_registered = True
        atexit.register(DeerFlowResearchRunner.cleanup_all)

        original = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        }
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original[signal.SIGHUP] = signal.getsignal(signal.SIGHUP)

        def cleanup_handler(signum, frame):
            DeerFlowResearchRunner.cleanup_all()
            prev = original.get(signum)
            if callable(prev):
                prev(signum, frame)            # 链式：通常是 SimulationRunner 的清理处理器
            elif prev == signal.SIG_IGN:
                return                         # 原本就忽略该信号 → 保持忽略
            else:
                # SIG_DFL 或未知（None）：恢复默认行为并自我终止
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(signal.SIGINT, cleanup_handler)
            signal.signal(signal.SIGTERM, cleanup_handler)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # 仅主线程可设置信号处理器；非主线程下 atexit 仍覆盖正常退出
            pass

    @classmethod
    def start(
        cls,
        prompt: str,
        *,
        mode: str = "full",
        project_name: Optional[str] = None,
        depth: Optional[str] = None,
        max_rounds: Optional[int] = None,
        language: Optional[str] = None,
        model: Optional[str] = None,
    ) -> PipelineState:
        """创建管线记录并在后台线程启动。立即返回（含 pipeline_id / task_id）。"""
        pipeline_id = f"pipe_{uuid.uuid4().hex[:12]}"
        PipelineManager.ensure_dirs(pipeline_id)

        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type=f"pipeline:{mode}",
            metadata={"pipeline_id": pipeline_id},
        )

        bands = RESEARCH_ONLY_BANDS if mode == "research_only" else STAGE_BANDS
        stages = {name: StageState(name=name) for name in bands.keys()}

        state = PipelineState(
            pipeline_id=pipeline_id,
            prompt=prompt,
            mode=mode,
            status="running",
            task_id=task_id,
            handoff_dir=PipelineManager.handoff_dir(pipeline_id),
            stages=stages,
        )
        state.options.update({
            "project_name": project_name or f"研究预测 {pipeline_id}",
            "depth": depth or Config.DEERFLOW_RESEARCH_DEPTH,
            "max_rounds": max_rounds,
            # T5.5: 每次运行的研究语言/模型覆盖。language 区分三态：None=用 Config 默认；
            # ""=auto（不传 --target-language，模型自选）；具体值=覆盖。model: None=用 Config 默认。
            "research_language": language,
            "research_model": model or None,
        })
        PipelineManager.save(state)

        cls._cancel_events[pipeline_id] = threading.Event()
        t = threading.Thread(
            target=cls._run,
            args=(state,),
            name=f"pipeline-{pipeline_id}",
            daemon=True,
        )
        cls._threads[pipeline_id] = t
        t.start()
        return state

    @classmethod
    def cancel(cls, pipeline_id: str) -> dict[str, Any]:
        """取消一条在飞管线。

        置位取消事件后，取消在下一个取消点生效：研究子进程组被立刻杀掉；
        OASIS 运行被 stop_simulation 停止；其余阶段在下一次进度回调时退出。
        本进程没有该管线的在飞线程（如后端重启后的孤儿）时，直接在持久化
        状态上标记 cancelled。

        Returns:
            {"ok": bool, "status": str}  status ∈ cancelling / cancelled / not_found / not_running
        """
        with cls._lifecycle_lock:
            data = PipelineManager.load(pipeline_id)
            if data is None:
                return {"ok": False, "status": "not_found"}
            if data.get("status") != "running":
                return {"ok": False, "status": "not_running"}

            event = cls._cancel_events.get(pipeline_id)
            thread = cls._threads.get(pipeline_id)
            if event is not None and thread is not None and thread.is_alive():
                event.set()
                logger.info(f"[{pipeline_id}] 收到取消请求，等待管线在取消点退出")
                return {"ok": True, "status": "cancelling"}

            # 孤儿（重启后遗留 running）：直接落盘为 cancelled（是用户决定，不是错误）
            PipelineManager.mark_failed(pipeline_id, "已被用户取消", status="cancelled")
            data = PipelineManager.load(pipeline_id) or {}
            tid = data.get("task_id")
            if tid:
                try:
                    TaskManager().fail_task(tid, "已被用户取消")
                except Exception:
                    pass
            return {"ok": True, "status": "cancelled"}

    @classmethod
    def _dependent_forks(cls, pipeline_id: str) -> list[str]:
        """返回把 pipeline_id 当作 base 的 fork 管线 id 列表。

        fork（what-if 情景）与其 base **共享同一个 handoff 目录**（research/ontology/graph
        产物），删除 base 会 rmtree 掉该目录，让所有 fork 的恢复/报告复用断裂。删除前据此
        守卫。尽力而为；读不到 options 时退化为加载完整状态。
        """
        deps: list[str] = []
        try:
            for p in PipelineManager.list_pipelines():
                pid = p.get("pipeline_id")
                if not pid or pid == pipeline_id:
                    continue
                opts = p.get("options")
                if not isinstance(opts, dict):
                    full = PipelineManager.load(pid)
                    opts = (full or {}).get("options") if isinstance(full, dict) else None
                if isinstance(opts, dict) and opts.get("base_pipeline_id") == pipeline_id:
                    deps.append(pid)
        except Exception:  # noqa: BLE001 — 守卫为尽力而为，扫描失败不阻断删除决策
            pass
        return deps

    @classmethod
    def delete_pipeline(cls, pipeline_id: str, force: bool = False) -> dict[str, Any]:
        """删除一条已结束的管线记录（含其 handoff 产物目录）。

        在飞管线必须先取消再删除——删除运行中的状态文件会让 _run 线程在下次
        落盘时凭空复活记录，且孤儿子进程无人回收。

        若该管线被其它 fork 当作 base（共享 handoff 目录），默认拒绝删除（返回
        has_dependents + 依赖列表），避免静默破坏 fork；force=True 可强制删除。

        Returns:
            {"ok": bool, "status": str}  status ∈ deleted / not_found / still_running / has_dependents
        """
        with cls._lifecycle_lock:
            live = cls._threads.get(pipeline_id)
            if live is not None and live.is_alive():
                return {"ok": False, "status": "still_running"}
            data = PipelineManager.load(pipeline_id)
            if data is None:
                return {"ok": False, "status": "not_found"}
            if not force:
                deps = cls._dependent_forks(pipeline_id)
                if deps:
                    logger.warning(
                        "[%s] 拒绝删除：仍有 %d 个 fork 依赖其 handoff 目录（%s…）。"
                        "请先删除这些 fork 或 force 删除。",
                        pipeline_id, len(deps), ", ".join(deps[:5]),
                    )
                    return {"ok": False, "status": "has_dependents", "dependents": deps}
            if data.get("status") == "running":
                # 持久化为 running 但本进程无线程 → 孤儿；先按取消语义落盘再删，
                # 这样即使删除中途失败，状态也不会停在 running 误导前端。
                PipelineManager.mark_failed(pipeline_id, "已被用户删除", status="cancelled")
                cls._kill_orphan_research(pipeline_id, data.get("research_pid"))
                # PAR-2：并行研究轨的各子进程 PID 逐一清理（同上，含 pipeline_id 校验）。
                for _tpid in (data.get("options") or {}).get("research_track_pids") or []:
                    cls._kill_orphan_research(pipeline_id, _tpid)
            ok = PipelineManager.delete(pipeline_id)
            if ok:
                cls._cancel_events.pop(pipeline_id, None)
                cls._threads.pop(pipeline_id, None)
                logger.info(f"[{pipeline_id}] 管线记录已删除")
                # W9-10：管线删除后顺带回收陈旧图谱（best-effort，绝不影响删除结果）。
                cls._gc_stale_graphs()
            return {"ok": ok, "status": "deleted" if ok else "not_found"}

    @classmethod
    def clean_terminal(cls, statuses: tuple[str, ...] = ("failed", "cancelled")) -> dict[str, Any]:
        """批量删除处于指定终态的管线记录（默认清理失败/已取消的运行）。

        running 与 completed 永不触碰；本进程仍有在飞线程的管线一并跳过。
        """
        deleted: list[str] = []
        skipped: list[str] = []
        for p in PipelineManager.list_pipelines():
            pid = p.get("pipeline_id")
            if not pid or p.get("status") not in statuses:
                continue
            result = cls.delete_pipeline(pid)
            (deleted if result["ok"] else skipped).append(pid)
        if deleted:
            logger.info(f"批量清理管线: 删除 {len(deleted)} 条（{', '.join(deleted[:5])}…）")
        return {"deleted": deleted, "skipped": skipped}

    @classmethod
    def resume(cls, pipeline_id: str, force: bool = False) -> PipelineState:
        """Resume a failed/cancelled pipeline in place, reusing existing artifacts.

        The pipeline keeps the same id so browser history, artifact paths, and
        local bookmarks remain valid. A fresh task id is assigned for progress
        polling, and the background runner skips completed/recoverable stages.

        ORCH-3 恢复态机收口（此前一天内被迫手工编辑 4 次 pipeline_state.json）：
          * status=running 但 owner 进程确证已死（_orphan_is_dead）→ 就地按取消语义回收后继续
            恢复，不再要求先重启后端触发 reconcile；
          * ``force=True`` 允许恢复一条 completed 但 pipeline_health 为 degraded/failed 的管线：
            仅把 REPORT 阶段重置为 pending（配合 ORCH-1 的损坏报告不复用守卫重生成报告）。
        """
        with cls._lifecycle_lock:
            # 持久化状态可能滞后（崩溃时写失败），线程注册表才是本进程在飞的真相。
            live = cls._threads.get(pipeline_id)
            if live is not None and live.is_alive():
                raise RuntimeError("管线仍在运行，无法恢复")

            data = PipelineManager.load(pipeline_id)
            if data is None:
                raise FileNotFoundError("管线不存在")
            _bad = PipelineManager.is_incompatible(data)  # I-4-4
            if _bad is not None:
                raise IncompatiblePipelineSchema(pipeline_id, _bad)
            if data.get("status") == "running":
                # ORCH-3(a): 无在飞线程 + owner 证据判死 → 就地回收（复用 reconcile 的判据），
                # 让「僵死的 running 孤儿」无需重启后端即可恢复。判活则维持 409。
                _stale_s = float(getattr(Config, "PIPELINE_HEARTBEAT_STALE_S", 120) or 120)
                if cls._orphan_is_dead(pipeline_id, _stale_s):
                    PipelineManager.mark_failed(
                        pipeline_id, "resume 时回收死管线（owner 进程已不存活）", status="cancelled")
                    data = PipelineManager.load(pipeline_id) or data
                    logger.warning("[%s] resume：running 孤儿 owner 已死，就地回收后继续恢复", pipeline_id)
                else:
                    raise RuntimeError("管线仍在运行，无法恢复")
            if data.get("status") == "completed":
                # ORCH-3(b): completed 但交付物健康降级/失败时，允许 force 重驱报告阶段。
                _ph = ((data.get("options") or {}).get("pipeline_health") or {})
                if not (force and _ph.get("status") in ("degraded", "failed")):
                    raise RuntimeError(
                        "管线已完成，无需恢复"
                        + ("（如需重生成降级报告，请带 force=true 重试）"
                           if _ph.get("status") in ("degraded", "failed") else "")
                    )

            state = PipelineState.from_dict(data)
            PipelineManager.ensure_dirs(pipeline_id)
            bands = RESEARCH_ONLY_BANDS if state.mode == "research_only" else STAGE_BANDS
            for name in bands.keys():
                state.stages.setdefault(name, StageState(name=name))

            if force and data.get("status") == "completed":
                # 仅重置 REPORT：研究/图谱/模拟产物保持复用。ORCH-1 的复用守卫会因交付物损坏
                # 拒绝复用旧报告并铸新 report_id。
                _rst = state.stages.setdefault(STAGE_REPORT, StageState(name=STAGE_REPORT))
                _rst.status = "pending"
                _rst.progress = 0
                _rst.error = None
                _rst.finished_at = None
                state.current_stage = STAGE_REPORT
                state.options["force_report_regen"] = _utcnow()

            failed_stage = state.current_stage
            if failed_stage and failed_stage in state.stages:
                st = state.stages[failed_stage]
                if st.status in ("failed", "cancelled"):
                    st.status = "pending"
                    st.error = None
                    st.finished_at = None

            task_manager = TaskManager()
            task_id = task_manager.create_task(
                task_type=f"pipeline:{state.mode}:resume",
                metadata={"pipeline_id": pipeline_id, "resumed_from_task_id": state.task_id},
            )
            state.task_id = task_id
            state.status = "running"
            state.error = None
            state.research_pid = None
            state.options["resumed_at"] = _utcnow()
            state.options["resume_count"] = int(state.options.get("resume_count") or 0) + 1
            PipelineManager.save(state)

            cls._cancel_events[pipeline_id] = threading.Event()
            t = threading.Thread(
                target=cls._run,
                args=(state,),
                name=f"pipeline-resume-{pipeline_id}",
                daemon=True,
            )
            cls._threads[pipeline_id] = t
            t.start()
            return state

    @classmethod
    def continue_to_full(cls, pipeline_id: str) -> PipelineState:
        """T6.2: 把一条已完成的 research_only 管线继续跑成完整管线（复用研究产物）。

        要求 mode==research_only && status==completed && 存在 research_report.md。翻转
        mode='full'，把完整 STAGE_BANDS 的非研究阶段标记为 pending（研究保持 completed），
        复用 resume() 的启动尾。``_run`` 会经 research_report.md 复用守卫跳过研究、且不再走
        research_only 早退。对 full / running 管线 → RuntimeError（由 API 层转 409）。
        """
        with cls._lifecycle_lock:
            live = cls._threads.get(pipeline_id)
            if live is not None and live.is_alive():
                raise RuntimeError("管线仍在运行，无法继续")

            data = PipelineManager.load(pipeline_id)
            if data is None:
                raise FileNotFoundError("管线不存在")
            _bad = PipelineManager.is_incompatible(data)  # I-4-4
            if _bad is not None:
                raise IncompatiblePipelineSchema(pipeline_id, _bad)
            if data.get("mode") != "research_only":
                raise RuntimeError("仅 research_only 管线可继续为完整管线")
            if data.get("status") != "completed":
                raise RuntimeError("research_only 管线尚未完成，无法继续")

            handoff_dir = PipelineManager.handoff_dir(pipeline_id)
            report_path = os.path.join(handoff_dir, "research_report.md")
            if not (os.path.exists(report_path) and os.path.getsize(report_path) > 0):
                raise RuntimeError("缺少 research_report.md，无法继续（请重跑研究）")

            state = PipelineState.from_dict(data)
            # 翻转为完整模式；研究阶段保持 completed，其余阶段重置为 pending
            state.mode = "full"
            for name in STAGE_BANDS.keys():
                st = state.stages.setdefault(name, StageState(name=name))
                if name == STAGE_RESEARCH:
                    st.status = "completed"
                    if st.progress != 100:
                        st.progress = 100
                else:
                    st.status = "pending"
                    st.error = None
                    st.finished_at = None
                    st.progress = 0

            task_manager = TaskManager()
            task_id = task_manager.create_task(
                task_type="pipeline:full:continue",
                metadata={"pipeline_id": pipeline_id, "continued_from_task_id": state.task_id},
            )
            state.task_id = task_id
            state.status = "running"
            state.error = None
            state.current_stage = STAGE_ONTOLOGY
            state.options["continued_to_full_at"] = _utcnow()
            PipelineManager.save(state)

            cls._cancel_events[pipeline_id] = threading.Event()
            t = threading.Thread(
                target=cls._run,
                args=(state,),
                name=f"pipeline-continue-{pipeline_id}",
                daemon=True,
            )
            cls._threads[pipeline_id] = t
            t.start()
            logger.info(f"[{pipeline_id}] research_only → full 继续，复用研究产物")
            return state

    @classmethod
    def fork(cls, base_pipeline_id: str, overlay: dict[str, Any]) -> PipelineState:
        """T4.6: 在 PREPARE 处分叉一个 what-if 情景管线（复用 base 的研究/本体/图谱）。

        overlay = {label, max_rounds?, influence_overrides{name:weight}, stance_overrides{name:stance},
                   injected_events[{round,poster_name,content}], as_of_shift?}。新管线复用 base 的
        project_id/graph_id/handoff（研究+本体+图谱直接命中复用守卫），仅重跑 prepare/run/report。
        要求 base 已完成图谱阶段。返回新建管线状态。
        """
        base = PipelineManager.load(base_pipeline_id)
        if base is None:
            raise FileNotFoundError("基础管线不存在")
        _bad = PipelineManager.is_incompatible(base)  # I-4-4
        if _bad is not None:
            raise IncompatiblePipelineSchema(base_pipeline_id, _bad)
        base_state = PipelineState.from_dict(base)
        if not base_state.graph_id:
            raise RuntimeError("基础管线尚未建图，无法分叉情景")

        label = str((overlay or {}).get("label") or "情景").strip()
        new_id = f"pipe_{uuid.uuid4().hex[:12]}"
        new_state = PipelineState(
            pipeline_id=new_id,
            prompt=base_state.prompt,
            mode="full",
            project_id=base_state.project_id,
            graph_id=base_state.graph_id,
            handoff_dir=base_state.handoff_dir or PipelineManager.handoff_dir(base_pipeline_id),
        )
        # 研究/本体/图谱标记完成 → 命中复用守卫；prepare/run/report 全新
        for name in STAGE_BANDS.keys():
            st = StageState(name=name)
            if name in (STAGE_RESEARCH, STAGE_ONTOLOGY, STAGE_GRAPH):
                st.status = "completed"
                st.progress = 100
            new_state.stages[name] = st
        new_state.options = {
            "scenario_overlay": overlay or {},
            "scenario_label": label,
            "base_pipeline_id": base_pipeline_id,
            "project_name": base_state.options.get("project_name"),
        }
        if (overlay or {}).get("max_rounds"):
            try:
                new_state.options["max_rounds"] = int(overlay["max_rounds"])
            except (TypeError, ValueError):
                pass

        PipelineManager.ensure_dirs(new_id)
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="pipeline:full:scenario",
            metadata={"pipeline_id": new_id, "base_pipeline_id": base_pipeline_id, "label": label},
        )
        new_state.task_id = task_id
        new_state.status = "running"
        new_state.current_stage = STAGE_PREPARE
        PipelineManager.save(new_state)

        with cls._lifecycle_lock:
            cls._cancel_events[new_id] = threading.Event()
            t = threading.Thread(target=cls._run, args=(new_state,), name=f"pipeline-scenario-{new_id}", daemon=True)
            cls._threads[new_id] = t
            t.start()
        logger.info(f"[{new_id}] 情景分叉自 {base_pipeline_id}（label={label}），复用图谱 {base_state.graph_id}")
        return new_state

    @staticmethod
    def apply_scenario_overlay_to_config(config: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """T4.6: 把情景 overlay 确定性地落到已生成的 simulation_config 上（就地修改并返回）。

        influence_overrides{name:weight} / stance_overrides{name:stance} 按 agent 名匹配覆盖
        （两路都覆盖，闭合 T3.6 旁路）；injected_events 追加为 scheduled_events（解析 poster_name
        → agent_id）；不破坏缺省字段。
        """
        from ..utils.actors import normalize_name
        agents = config.get("agent_configs") or []
        by_name = {normalize_name(a.get("entity_name", "")): a for a in agents if a.get("entity_name")}

        inf = (overlay or {}).get("influence_overrides") or {}
        for name, weight in inf.items():
            a = by_name.get(normalize_name(str(name)))
            if a is not None:
                try:
                    a["influence_weight"] = float(weight)
                except (TypeError, ValueError):
                    pass
        stc = (overlay or {}).get("stance_overrides") or {}
        for name, stance in stc.items():
            a = by_name.get(normalize_name(str(name)))
            if a is not None:
                a["stance"] = str(stance)

        injected = (overlay or {}).get("injected_events") or []
        if injected:
            ec = config.setdefault("event_config", {})
            sched = ec.setdefault("scheduled_events", [])
            # 影响力最高 agent 作兜底发布者
            fallback = max(agents, key=lambda x: x.get("influence_weight", 1.0), default=None)
            fb_id = fallback.get("agent_id") if fallback else 0
            fb_name = fallback.get("entity_name") if fallback else ""
            for ev in injected:
                poster_name = str(ev.get("poster_name", "") or "").strip()
                a = by_name.get(normalize_name(poster_name)) if poster_name else None
                sched.append({
                    "round": int(ev.get("round", 0) or 0),
                    "content": str(ev.get("content", "") or ""),
                    "date": ev.get("date"),
                    "poster_agent_id": a.get("agent_id") if a else fb_id,
                    "poster_name": a.get("entity_name") if a else fb_name,
                    "is_scenario_injection": True,
                })
        return config

    # -- NEXTSTEPS P0-3: 同问多种子集成 -----------------------------------
    @staticmethod
    def _infer_horizon_date(prompt: Any, actors: Any) -> Optional[str]:
        """NEXTSTEPS P1-2：从问题/central_question 解析一个预测时间范围年份 → 'YYYY-12-31'。

        供子进程把每轮映射到日历日期（as_of→horizon 线性）。无可解析年份 → None（不做日期映射）。
        """
        text = str(prompt or "")
        if isinstance(actors, dict):
            text += " " + str(actors.get("central_question") or "")
        # 数字边界（非 \b：\b 在中日韩字符旁不触发，"2027年" 取不到年份）。
        years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
        if not years:
            return None
        return f"{max(years)}-12-31"

    @staticmethod
    def _agreement_to_confidence(agreement: Any) -> str:
        """把 inter-seed 一致度 ∈[0,1] 映射为报告信心（高一致=high）。无效值→medium。"""
        try:
            a = float(agreement)
        except (TypeError, ValueError):
            return "medium"
        if a >= 0.75:
            return "high"
        if a >= 0.45:
            return "medium"
        return "low"

    @staticmethod
    def _read_report_forecast(report_id: Optional[str]) -> Optional[dict]:
        """读取某报告目录下的 forecast.json（不存在/损坏→None）。"""
        if not report_id:
            return None
        try:
            from .report_agent import ReportManager
            fpath = os.path.join(ReportManager._get_report_folder(report_id), "forecast.json")
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:  # noqa: BLE001 — 读不到就当无集成样本
            return None
        return None

    def _maybe_run_seed_ensemble(self, state: "PipelineState", project: Any, graph_id: Optional[str],
                                 actors: Any, research: dict, report_md: str) -> None:
        """NEXTSTEPS P0-3: 同问多种子集成。

        对**同一张图谱**用不同 SIM_SEED 多跑 (prepare→run→report)，收集每次的 forecast.json
        （含主跑），聚合为 ensemble_forecast.json，并把 inter-seed 一致度映射成报告信心回写主
        forecast.json。门控：N_FORECAST_SEEDS>1、full 模式、结构化预测已开、尚未集成。串行、
        有界、带停滞看门狗；任一额外种子失败仅告警跳过；有效样本<2 则不落集成文件（degrade-safe）。
        默认 N=1 → 该方法直接返回（与现状逐字节一致）。
        """
        try:
            n_seeds = max(1, int(getattr(Config, "N_FORECAST_SEEDS", 1) or 1))
        except (TypeError, ValueError):
            n_seeds = 1
        if (n_seeds <= 1 or state.mode != "full"
                or not getattr(Config, "REPORT_STRUCTURED_FORECAST", True)
                or state.options.get("ensemble_done")):
            return
        if not (project and graph_id and state.report_id):
            return
        from ..utils.atomic import write_json_atomic
        primary_fc = self._read_report_forecast(state.report_id)
        if not primary_fc or not primary_fc.get("scenarios"):
            logger.info("[%s] 主报告无结构化预测，跳过多种子集成", state.pipeline_id)
            return
        forecasts: list = [primary_fc]
        extra_runs: list = []
        base_seed = int(getattr(Config, "SIM_SEED", 0) or 0)
        _mr = state.options.get("max_rounds") or (Config.OASIS_DEFAULT_MAX_ROUNDS or None)
        max_rounds = int(_mr) if _mr else None
        handoff_dir = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
        # 派生互异种子（base=0 时也确定性互异）；每个种子跑一次独立 (prepare→run→report)。
        seed_jobs = [(k, (base_seed or 0) + k * 7919) for k in range(2, n_seeds + 1)]
        cancel_ev0 = type(self)._cancel_events.get(state.pipeline_id)
        if cancel_ev0 is not None and cancel_ev0.is_set():
            raise PipelineCancelled("多种子集成期间被取消")

        # W9-5：主跑的情景脊柱（名 + 判定标准）钉给每个额外种子——种子间共享同一组命名情景
        # （概率自由），集成聚合才有稳定的对齐坐标（此前 3 种子自由起名 → 11 桶全 support=1）。
        _spine: Optional[list] = None
        try:
            _spine = [
                {"name": s.get("name"), "resolution_criteria": s.get("resolution_criteria")}
                for s in (primary_fc.get("scenarios") or [])
                if isinstance(s, dict) and s.get("name")
            ] or None
        except Exception:  # noqa: BLE001 — 脊柱纯增强，坏数据回退自由起名
            _spine = None

        # W9-2：集成 checkpoint + 遥测阶段带 + 心跳。此前集成运行在「report 完成之后」的
        # 无保护窗口：无 checkpoint、心跳陈旧、墙钟不归属任何阶段——两条失败跑都死在这里。
        from ..utils.telemetry import set_run_context as _set_run_ctx, set_stage as _set_stage
        _ckpt_path = os.path.join(handoff_dir, "ensemble_checkpoint.json")
        _done_seeds: dict[int, dict] = {}
        _prev_ckpt = _read_json(_ckpt_path)
        if isinstance(_prev_ckpt, dict):
            for _row in (_prev_ckpt.get("completed") or []):
                if not isinstance(_row, dict):
                    continue
                try:
                    _done_seeds[int(_row.get("seed"))] = _row
                except (TypeError, ValueError):
                    continue
        _ckpt_lock = threading.Lock()

        def _persist_ckpt() -> None:
            """把已完成种子清单原子落盘（调用方持 _ckpt_lock）。失败仅告警，不拦集成。"""
            try:
                write_json_atomic(_ckpt_path, {
                    "schema_version": 1,
                    "pipeline_id": state.pipeline_id,
                    "primary_report_id": state.report_id,
                    "n_seeds_requested": n_seeds,
                    "completed": list(_done_seeds.values()),
                    "updated_at": _utcnow(),
                })
            except Exception as _ce:  # noqa: BLE001
                logger.warning("[%s] ensemble_checkpoint.json 落盘失败（忽略）: %s",
                               state.pipeline_id, _ce)

        state.options.setdefault("ensemble_wall", {})["started_at"] = _utcnow()
        try:
            _set_stage("ensemble")
        except Exception:  # noqa: BLE001
            pass

        # PAR-3：额外种子并行跑。_run_one_seed 线程安全（各种子独立 simulation_id → 独立
        # 目录/DB/文件式 IPC/仅注入子进程 env；SimulationRunner 类级映射均按 sim_id 独立键；
        # 共享的 graph_id 由 Zep 服务端处理并发写，串行版本本就共享同一张图）。并发度
        # ENSEMBLE_SEED_CONCURRENCY（默认 2、上限 3）；=1 或仅 1 个额外种子时走串行（逐字节
        # 复现旧行为）。共享 cancel_event + 每种子内建停滞看门狗均保留（在 _run_one_seed 内）。
        try:
            seed_conc = max(1, min(3, int(getattr(Config, "ENSEMBLE_SEED_CONCURRENCY", 2) or 2)))
        except (TypeError, ValueError):
            seed_conc = 2

        def _do_seed(k: int, seed: int) -> None:
            """跑一个额外种子并把结果并入 forecasts/extra_runs（GIL 下 list.append 线程安全）。

            W9-2 幂等：checkpoint 里已完成、且其报告仍能读出结构化预测的种子直接复用结果，
            重启后 resume 不再重烧已完成的种子。ThreadPoolExecutor 不继承 contextvars，
            这里显式重设 run/stage 上下文，种子的 LLM 调用才会归入 ensemble 阶段带
            （此前落 '_global' 桶，per-run 成本失真）。
            """
            try:
                _set_run_ctx(state.pipeline_id, "ensemble")
            except Exception:  # noqa: BLE001
                pass
            _prev = _done_seeds.get(seed)
            if _prev and _prev.get("report_id"):
                _fc_prev = self._read_report_forecast(_prev.get("report_id"))
                if _fc_prev and _fc_prev.get("scenarios"):
                    logger.info("[%s] 集成种子 %s 已在 checkpoint 中完成，复用其报告 %s",
                                state.pipeline_id, seed, _prev.get("report_id"))
                    extra_runs.append({"seed": seed,
                                       "simulation_id": _prev.get("simulation_id"),
                                       "report_id": _prev.get("report_id"),
                                       "resumed_from_checkpoint": True})
                    forecasts.append(_fc_prev)
                    return
            sim_id, rid, fc = self._run_one_seed(
                state, project, graph_id, actors, research, report_md,
                seed=seed, max_rounds=max_rounds, scenario_spine=_spine,
            )
            with _ckpt_lock:
                _done_seeds[seed] = {
                    "k": k, "seed": seed, "simulation_id": sim_id, "report_id": rid,
                    "has_forecast": bool(fc and fc.get("scenarios")),
                    "completed_at": _utcnow(),
                }
                _persist_ckpt()
            try:  # W9-2：每完成一个种子即刷新心跳（集成窗口内心跳不再冻结）
                PipelineManager.touch_heartbeat(state.pipeline_id, pid=os.getpid())
            except Exception:  # noqa: BLE001
                pass
            extra_runs.append({"seed": seed, "simulation_id": sim_id, "report_id": rid})
            if fc and fc.get("scenarios"):
                forecasts.append(fc)

        try:
            if seed_conc <= 1 or len(seed_jobs) <= 1:
                # 串行路径（与今日逐字节一致）
                for k, seed in seed_jobs:
                    cancel_ev = type(self)._cancel_events.get(state.pipeline_id)
                    if cancel_ev is not None and cancel_ev.is_set():
                        raise PipelineCancelled("多种子集成期间被取消")
                    st = state.stages.setdefault(STAGE_REPORT, StageState(name=STAGE_REPORT))
                    st.message = f"多种子集成 {k}/{n_seeds}（种子 {seed}）…"
                    if state.owner_boot_id is not None:  # W9-2：save 不得用陈旧心跳覆写看护线程
                        state.heartbeat_at = _utcnow()
                    PipelineManager.save(state)
                    try:
                        _do_seed(k, seed)
                    except PipelineCancelled:
                        raise
                    except Exception as _se:  # noqa: BLE001 — 单个种子失败不拖垮集成
                        logger.warning("[%s] 集成种子 %s 失败（跳过）: %s", state.pipeline_id, k, _se)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                st = state.stages.setdefault(STAGE_REPORT, StageState(name=STAGE_REPORT))
                st.message = f"多种子集成：并行 {len(seed_jobs)} 个额外种子（并发 {seed_conc}）…"
                if state.owner_boot_id is not None:  # W9-2
                    state.heartbeat_at = _utcnow()
                PipelineManager.save(state)
                cancelled: Optional[PipelineCancelled] = None
                with ThreadPoolExecutor(max_workers=seed_conc, thread_name_prefix="ens-seed") as ex:
                    futs = {ex.submit(_do_seed, k, seed): (k, seed) for k, seed in seed_jobs}
                    for fut in as_completed(futs):
                        k, seed = futs[fut]
                        try:
                            fut.result()
                        except PipelineCancelled as pc:
                            cancelled = pc  # 取消须穿透（其余种子按同一 cancel_event 各自停机）
                        except Exception as _se:  # noqa: BLE001 — 单个种子失败不拖垮集成
                            logger.warning("[%s] 集成种子 %s 失败（跳过）: %s", state.pipeline_id, k, _se)
                if cancelled is not None:
                    raise cancelled
            if len(forecasts) < 2:
                logger.info("[%s] 有效集成样本<2，不写 ensemble_forecast.json", state.pipeline_id)
                state.options["ensemble_done"] = True
                PipelineManager.save(state)
                return
            from .ensemble import aggregate_forecasts
            agg = aggregate_forecasts(forecasts)
            agg["n_seeds_requested"] = n_seeds
            agg["extra_runs"] = extra_runs
            agreement = agg.get("agreement")
            agg["confidence"] = self._agreement_to_confidence(agreement)
            # 落 ensemble_forecast.json（handoff + 主报告目录），并把一致度/信心回写主 forecast.json
            try:
                write_json_atomic(os.path.join(handoff_dir, "ensemble_forecast.json"), agg)
                state.artifacts["ensemble_forecast"] = os.path.join(handoff_dir, "ensemble_forecast.json")
            except Exception:  # noqa: BLE001
                pass
            try:
                from .report_agent import ReportManager
                rfolder = ReportManager._get_report_folder(state.report_id)
                write_json_atomic(os.path.join(rfolder, "ensemble_forecast.json"), agg)
                primary_fc["confidence"] = agg["confidence"]
                primary_fc["ensemble"] = {
                    "n_runs": agg.get("n_runs"), "agreement": agreement,
                    "scenarios": agg.get("scenarios"),
                }
                write_json_atomic(os.path.join(rfolder, "forecast.json"), primary_fc)
            except Exception as _we:  # noqa: BLE001
                logger.warning("[%s] 写集成结果失败: %s", state.pipeline_id, _we)
            state.options["ensemble_done"] = True
            state.options["ensemble"] = {
                "n_runs": agg.get("n_runs"), "agreement": agreement, "confidence": agg["confidence"],
            }
            PipelineManager.save(state)
            logger.info("[%s] 多种子集成完成: n=%s, 一致度=%s, 信心=%s",
                        state.pipeline_id, agg.get("n_runs"), agreement, agg["confidence"])
        finally:
            # W9-2：无论正常/取消/异常，把集成墙钟收口进 options（遥测的 'ensemble' 阶段带）
            # 并恢复 LLM 计量阶段上下文；save 前刷新内存心跳，避免用陈旧值覆写看护线程。
            state.options.setdefault("ensemble_wall", {})["finished_at"] = _utcnow()
            if state.owner_boot_id is not None:
                state.heartbeat_at = _utcnow()
            try:
                PipelineManager.save(state)
            except Exception:  # noqa: BLE001
                pass
            try:
                _set_stage(STAGE_REPORT)
            except Exception:  # noqa: BLE001
                pass
            self._flush_run_telemetry(state)  # W9-3：集成窗口结束即落一版遥测

    def _run_one_seed(self, state: "PipelineState", project: Any, graph_id: str,
                      actors: Any, research: dict, report_md: str, *,
                      seed: int, max_rounds: Optional[int],
                      scenario_spine: Optional[list] = None) -> tuple:
        """对同一图谱跑一次额外 (prepare→run→report)，返回 (sim_id, report_id, forecast|None)。

        自包含、串行、运行在管线线程内；不触碰主 sim/report 的 id 与状态。带停滞看门狗。
        W9-5 ``scenario_spine``：主跑的情景脊柱（名+判定标准），钉给种子的 ReportAgent 使
        种子对同一组命名情景打分（概率自由）；报告链尚未支持该参数时回退旧签名。
        """
        sim_manager = SimulationManager()
        _is_http = bool(Config.PROVIDER_META.get(Config.LLM_PROVIDER, {}).get('openai_compat'))
        sim_state = sim_manager.create_simulation(
            project.project_id, graph_id, enable_twitter=True, enable_reddit=True)
        sim_id = sim_state.simulation_id
        # SIM-11 (pairs with SIM-7): HTTP/openai-compat providers tolerate higher
        # persona fan-out; raise the default 8→16 (configurable via PARALLEL_PROFILE_COUNT).
        # CLI providers stay capped at 3 (local CLI throughput bound).
        _pp = int(getattr(Config, "PARALLEL_PROFILE_COUNT", 16) or 16)
        # W9-5：与主路径（PREPARE 阶段）对齐——把 GRAPH 阶段持久化的中心度先验也喂给种子跑的
        # salience 排序（此前种子跑漏传 graph_priors，agent 选池与主跑系统性不一致）。
        _graph_priors = None
        try:
            _graph_priors = _read_json(os.path.join(
                state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id),
                "graph_priors.json"))
        except Exception:  # noqa: BLE001
            _graph_priors = None
        sim_manager.prepare_simulation(
            simulation_id=sim_id,
            simulation_requirement=state.prompt,
            document_text=report_md,
            parallel_profile_count=_pp if _is_http else 3,
            actors=actors,
            graph_priors=_graph_priors,  # W9-5: 与主路径一致（P3-7 中心度先验）
            max_rounds=max_rounds,  # PREP-1: 集成种子跑同样按真实执行窗排期
            research_language=state.options.get("research_language"),  # PREP-4
        )
        run_kwargs: dict[str, Any] = {"platform": "parallel", "sim_seed": int(seed)}
        if max_rounds:
            run_kwargs["max_rounds"] = int(max_rounds)
        if Config.SIM_GRAPH_FEEDBACK and graph_id:
            run_kwargs["enable_graph_memory_update"] = True
            run_kwargs["graph_id"] = graph_id
        SimulationRunner.start_simulation(simulation_id=sim_id, **run_kwargs)
        cancel_ev = type(self)._cancel_events.get(state.pipeline_id)
        last_progress_at = time.monotonic()
        last_round = -1
        try:
            stall_s = float(getattr(Config, "PIPELINE_RUN_STALL_S", 1800) or 1800)
        except (TypeError, ValueError):
            stall_s = 1800.0
        # W9-2：种子轮询内的心跳刷新节律（与看护线程同参）。
        try:
            _hb_interval = float(getattr(Config, "PIPELINE_HEARTBEAT_INTERVAL_S", 30) or 30)
        except (TypeError, ValueError):
            _hb_interval = 30.0
        _last_hb = time.monotonic()
        while True:
            if cancel_ev is not None and cancel_ev.is_set():
                try:
                    SimulationRunner.stop_simulation(sim_id)
                except Exception:  # noqa: BLE001
                    pass
                raise PipelineCancelled("多种子集成期间被取消")
            rs = SimulationRunner.get_run_state(sim_id)
            if rs is None:
                raise RuntimeError("集成种子模拟运行状态丢失")
            cur = getattr(rs, "current_round", 0) or 0
            if cur != last_round:
                last_round = cur
                last_progress_at = time.monotonic()
            if rs.runner_status == RunnerStatus.COMPLETED:
                break
            if rs.runner_status in (RunnerStatus.FAILED, RunnerStatus.STOPPED):
                raise RuntimeError(f"集成种子模拟未正常结束: {rs.runner_status}")
            if stall_s > 0 and (time.monotonic() - last_progress_at) > stall_s:
                try:
                    SimulationRunner.stop_simulation(sim_id)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(f"集成种子模拟约 {int(stall_s)}s 无进展，看门狗终止")
            # W9-2：种子轮询期间按心跳节律刷新 heartbeat_at——集成窗口内心跳不再冻结，
            # 别的进程的 reconcile_orphans 能看到「这条管线还活着」。
            if (time.monotonic() - _last_hb) >= _hb_interval:
                _last_hb = time.monotonic()
                try:
                    PipelineManager.touch_heartbeat(state.pipeline_id, pid=os.getpid())
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(5)
        # 反馈写入器排空（与主跑一致），保证报告读到完整图谱
        if Config.SIM_GRAPH_FEEDBACK and graph_id:
            SimulationRunner.join_monitor_thread(sim_id, timeout=30)
            try:
                ZepGraphMemoryManager.stop_updater(sim_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            SimulationRunner.write_run_summary(sim_id)
        except Exception:  # noqa: BLE001
            pass
        rid = f"report_{uuid.uuid4().hex[:12]}"
        _agent_kwargs: dict[str, Any] = dict(
            graph_id=graph_id, simulation_id=sim_id, simulation_requirement=state.prompt,
            situation_brief=situation_brief(actors), actors=actors,
            sources=research.get("sources"), research_report=report_md,
        )
        # W9-5：把主跑情景脊柱钉给种子报告（scenario_spine 参数由报告链工作流并行落地；
        # 尚未支持时 TypeError → 回退旧签名，落地顺序无关）。
        try:
            if scenario_spine:
                agent = ReportAgent(scenario_spine=scenario_spine, **_agent_kwargs)
            else:
                agent = ReportAgent(**_agent_kwargs)
        except TypeError:
            logger.info("[%s] ReportAgent 尚未支持 scenario_spine，种子 %s 回退自由情景命名",
                        state.pipeline_id, seed)
            agent = ReportAgent(**_agent_kwargs)
        agent.generate_report(report_id=rid)
        return sim_id, rid, self._read_report_forecast(rid)

    # -- 内部：进度辅助 ----------------------------------------------------

    @staticmethod
    def _global_from_stage(mode: str, stage: str, local_pct: int, dynamic_bands: Optional[dict] = None) -> int:
        base = RESEARCH_ONLY_BANDS if mode == "research_only" else STAGE_BANDS
        # T6.7: 已知真实成本信号时用动态权重（仅 full 模式）；否则回退静态档位。
        bands = dynamic_bands if (dynamic_bands and mode != "research_only") else base
        band = bands.get(stage) or base.get(stage, (0, 100))
        lo, hi = band[0], band[1]
        local_pct = max(0, min(100, local_pct))
        return int(lo + (hi - lo) * local_pct / 100)

    @staticmethod
    def _recompute_dynamic_bands(
        state: "PipelineState",
        chunk_count: Optional[int] = None,
        total_rounds: Optional[int] = None,
        section_count: Optional[int] = None,
    ) -> None:
        """T6.7: 用已知成本信号（chunk 数 / 轮数 / 章节数）按比例重排 graph/run/report 的全局区间。

        research/ontology/prepare 区间保持静态；把这三段之后到 100 的剩余区间按成本权重重新切分，
        让深跑（多 chunk/多轮）时图谱/模拟阶段占更大份额，进度条与 ETA 更诚实。结果写入
        state.options['dynamic_bands']，被 _global_from_stage 读取。信号不全则按当前已知部分估计。"""
        # research/ontology 静态；动态区间覆盖 graph(40)→report(100)
        dyn_start = STAGE_BANDS[STAGE_GRAPH][0]      # 40
        dyn_total = 100 - dyn_start                  # 60
        # 成本权重（带兜底，避免 0）
        prev = state.options.get("cost_signals", {}) if isinstance(state.options.get("cost_signals"), dict) else {}
        cc = chunk_count if chunk_count is not None else prev.get("chunk_count")
        tr = total_rounds if total_rounds is not None else prev.get("total_rounds")
        sc = section_count if section_count is not None else prev.get("section_count")
        state.options["cost_signals"] = {"chunk_count": cc, "total_rounds": tr, "section_count": sc}
        w_graph = float(cc or 20)        # 每 chunk ~1 次 LLM 抽取
        w_prepare = 8.0                  # prepare 较轻且较稳定
        w_run = float((tr or 24) * 1.5)  # 每轮多次 agent LLM 调用
        w_report = float((sc or 6) * 6)  # 每章多次工具调用
        weights = [("graph", w_graph), ("prepare", w_prepare), ("run", w_run), ("report", w_report)]
        tot = sum(w for _, w in weights) or 1.0
        bands: dict[str, list] = {
            STAGE_RESEARCH: list(STAGE_BANDS[STAGE_RESEARCH]),
            STAGE_ONTOLOGY: list(STAGE_BANDS[STAGE_ONTOLOGY]),
        }
        cursor = float(dyn_start)
        for name, w in weights:
            seg = dyn_total * (w / tot)
            bands[name] = [int(round(cursor)), int(round(cursor + seg))]
            cursor += seg
        bands["report"][1] = 100  # 收尾对齐 100，避免取整漂移
        state.options["dynamic_bands"] = bands

    def _make_stage_updater(self, state: PipelineState, stage: str):
        task_manager = TaskManager()
        # 把后续该阶段的 LLM 调用归属到此 stage（I-5-2 per-stage rollup）。
        try:
            from ..utils.telemetry import set_stage
            set_stage(stage)
        except Exception:
            pass

        def update(local_pct: int, message: str):
            # 取消点：各阶段内部都会频繁回调进度，在这里抬升取消请求，
            # 使取消无需等到阶段边界。
            ev = PipelineOrchestrator._cancel_events.get(state.pipeline_id)
            if ev is not None and ev.is_set():
                raise PipelineCancelled("管线已被用户取消")
            st = state.stages.get(stage)
            if st is None:
                st = StageState(name=stage)
                state.stages[stage] = st
            st.status = "running"
            st.progress = max(0, min(100, int(local_pct)))
            st.message = message
            if st.started_at is None:
                st.started_at = _utcnow()
            state.current_stage = stage
            # I-5-6: 记录最近一次进度信号的壁钟时间戳，供状态 API 计算 elapsed/stale，
            # 让 UI 把「长时间无进度」诚实地呈现为「仍在思考」而非「卡死的进度条」。
            # I-4-1: 进度回调本身即确凿的存活信号，顺手把 heartbeat_at 也刷新，避免随后的
            # save(state) 用陈旧的内存值覆盖看护线程刚原子写入的新心跳（两者就此保持一致）。
            _hb_now = _utcnow()
            state.last_progress_at = _hb_now
            if state.owner_boot_id is not None:
                state.heartbeat_at = _hb_now
            state.global_progress = self._global_from_stage(
                state.mode, stage, local_pct, state.options.get("dynamic_bands")
            )
            # I-4-6: 在长阶段运行中机会性地登记已落盘的「临时产物」深链（best-effort，
            # 不覆盖完成时登记的正式产物），让用户在阶段完成前就能查看增量证据。
            self._register_partial_artifacts(state, stage)
            PipelineManager.save(state)
            # W9-3：进度回调即机会性遥测 flush 检查点（每新增 N 次 LLM 调用落一版账）。
            try:
                self._maybe_flush_run_telemetry(state)
            except Exception:  # noqa: BLE001
                pass
            if state.task_id:
                task_manager.update_task(
                    state.task_id,
                    progress=state.global_progress,
                    message=f"[{stage}] {message}",
                )

        return update

    def _complete_stage(self, state: PipelineState, stage: str, message: str = "完成"):
        st = state.stages.setdefault(stage, StageState(name=stage))
        st.status = "completed"
        st.progress = 100
        st.message = message
        st.finished_at = _utcnow()
        state.global_progress = self._global_from_stage(
            state.mode, stage, 100, state.options.get("dynamic_bands")
        )
        try:
            self._record_stage_artifacts(state, stage)  # T6.3
        except Exception:
            pass
        PipelineManager.save(state)
        # W9-3：阶段转换必落一版遥测账（重启只丢「上一次阶段边界之后」的增量）。
        self._flush_run_telemetry(state)

    # ---------------------------------------------------------------- S1 health gate
    # The corpus review found 8/13 runs reported status=completed/100%/error=null while
    # the deliverable was actually broken (all-placeholder reports, no forecast.json, hollow
    # sims). The pipeline marked success on stage *return* without validating artifacts, so
    # every other defect was invisible. These methods assess the real deliverable and either
    # HARD-FAIL the run (so it shows as failed, not falsely completed) or record a degraded
    # health block consumed by the status API + the report's simulation-caveat logic.
    @staticmethod
    def _sim_dir(sim_id: str) -> str:
        return os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "uploads", "simulations", sim_id))

    def _spawn_run_stall_watchdog(self, pipeline_id: str, sim_id: str, stall_s: float) -> dict:
        """QUALITY-OPT C1/C2 (live-surfaced): an INDEPENDENT daemon watchdog for the RUN stage.

        The inline poll-loop watchdog cannot fire when the poll itself blocks on a wedged sim's
        IPC (observed: both LLM providers exhausted → sim deadlocked at a round for 4.5h while the
        pipeline still showed 'running'). This thread reads run_state.json FROM DISK (never blocks
        on the sim), and if the round hasn't advanced within stall_s it force-stops the sim
        subprocess — which makes the poll loop's next get_run_state return STOPPED and fail the run
        honestly instead of hanging forever. Returns a control dict; caller sets ctl['stop']=True.
        """
        import threading
        ctl = {"stop": False}
        if stall_s <= 0:
            return ctl
        rsp = os.path.join(self._sim_dir(sim_id), "run_state.json")

        def _wd() -> None:
            last_round = None
            last_prog = time.monotonic()
            while not ctl["stop"]:
                time.sleep(30)
                if ctl["stop"]:
                    return
                try:
                    cur = None
                    if os.path.exists(rsp):
                        with open(rsp, encoding="utf-8") as f:
                            rs = json.load(f)
                        cur = rs.get("current_round")
                        if rs.get("completed") or rs.get("error"):
                            return  # sim reached a terminal state; poll loop handles it
                    if cur != last_round:
                        last_round = cur
                        last_prog = time.monotonic()
                    elif (time.monotonic() - last_prog) > stall_s:
                        logger.error("[%s] 独立看门狗：模拟 %ds 无轮次推进（卡在 round %s，疑似双 provider 耗尽），"
                                     "强制停止子进程", pipeline_id, int(stall_s), cur)
                        try:
                            SimulationRunner.stop_simulation(sim_id)
                        except Exception as _e:  # noqa: BLE001
                            logger.warning("[%s] 看门狗停止模拟失败: %s", pipeline_id, _e)
                        return
                except Exception:  # noqa: BLE001 — watchdog must never crash
                    pass

        threading.Thread(target=_wd, name=f"run-stall-wd-{sim_id[:8]}", daemon=True).start()
        return ctl

    def _assess_report_health(self, report_id: Optional[str]) -> "tuple[str, list[str], dict]":
        """Hard-fail if the report is fundamentally empty (all placeholders / no forecast /
        trivially short); degraded if some sections are placeholders."""
        import glob
        issues: list[str] = []
        if not report_id:
            return "failed", ["no report_id was produced"], {}
        try:
            folder = ReportManager._get_report_folder(report_id)
        except Exception:  # noqa: BLE001
            return "failed", ["report folder unresolved"], {}
        secs = sorted(glob.glob(os.path.join(folder, "section_*.md")))
        total = len(secs)
        placeholder = 0
        for s in secs:
            try:
                body = open(s, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            # ORCH-1(2): 占位符判定收紧为 report_agent 实际写入的失败模板——
            # SECTION_FAILURE_PLACEHOLDER 与「LLM 返回空响应」变体均以「（本章节生成失败：」
            # 开头。旧的 '生成失败' / '本章节'+'失败' 松散子串会把正文里合法讨论
            # 「××失败」的章节误判为占位符。另外，含图表标记的短章节（一张 Mermaid/内嵌图
            # + 简短图注是合法产出）豁免 <200 字符规则，不再按占位符计。
            _is_placeholder = "（本章节生成失败：" in body
            if not _is_placeholder and len(body.strip()) < 200:
                _is_placeholder = not any(
                    m in body for m in ("![", "<img", "<svg", "```mermaid"))
            if _is_placeholder:
                placeholder += 1
        fc_ok = False
        fcp = os.path.join(folder, "forecast.json")
        q_issues: list[str] = []
        if os.path.exists(fcp):
            try:
                fc = json.load(open(fcp, encoding="utf-8"))
                fc_ok = bool(fc.get("scenarios") or fc.get("binary_forecasts"))
                # surface the report-side audit findings (S2/S11/S12) as degraded signals
                q = fc.get("quality") or {}
                if (q.get("quote_provenance") or {}).get("ungrounded"):
                    q_issues.append(f"{q['quote_provenance']['ungrounded']} ungrounded/laundered quote(s) (S2)")
                if (q.get("numeric_consistency") or {}).get("mismatch_count"):
                    q_issues.append(f"{q['numeric_consistency']['mismatch_count']} prose-vs-forecast probability mismatch(es) (S11)")
                if (q.get("implausible_stats") or {}).get("count"):
                    q_issues.append(f"{q['implausible_stats']['count']} implausible headline stat(s) (S12)")
                bq = fc.get("binary_quality") or {}
                if bq and not bq.get("passed", True):
                    q_issues.append("binary-forecast conviction/objectivity gate failed (A3/A4): " + "；".join(bq.get("issues", [])[:2]))
                # XRUN-1(c): 二元预测对模拟不敏感（与另一份报告输出同一概率向量）→ 降级信号。
                if (q.get("sim_insensitivity") or {}).get("issue"):
                    q_issues.append(
                        "binary forecasts insensitive to simulation (identical vector to "
                        f"{(q.get('sim_insensitivity') or {}).get('other_report_id')})")
                # XRUN-16(1): 钉定骨架与最终 forecast 之间情景数漂移。
                _scd = q.get("scenario_count_drift") or {}
                if _scd:
                    q_issues.append(
                        f"scenario count drifted {_scd.get('pinned')}→{_scd.get('final')} "
                        "between pinned spine and final forecast")
                # XRUN-16(2): 发布门的引用覆盖率失败已在报告侧写进 quality.issues，折入健康块。
                for _qi in (q.get("issues") or []):
                    if isinstance(_qi, str) and _qi.startswith("定量声明引用覆盖率"):
                        q_issues.append(_qi)
            except (OSError, ValueError):
                fc_ok = False
        fr = os.path.join(folder, "full_report.md")
        fr_len = os.path.getsize(fr) if os.path.exists(fr) else 0
        meta = {"sections": total, "placeholder_sections": placeholder,
                "forecast_ok": fc_ok, "full_report_bytes": fr_len}
        hard = False
        if total and placeholder >= total:
            hard = True
            issues.append(f"all {total} report sections are failure placeholders")
        elif placeholder:
            issues.append(f"{placeholder}/{total} report sections are failure placeholders")
        if not fc_ok and getattr(Config, "REPORT_STRUCTURED_FORECAST", True):
            hard = True
            issues.append("forecast.json missing or empty (no scenarios/binary_forecasts)")
        if fr_len and fr_len < 2000:
            hard = True
            issues.append(f"full_report.md is only {fr_len} bytes (effectively empty)")
        issues.extend(q_issues)  # S2/S11/S12/binary-gate audit findings → degraded signals
        meta["quality_issues"] = q_issues
        health = "failed" if hard else ("degraded" if issues else "ok")
        return health, issues, meta

    def _assess_run_health(self, sim_id: Optional[str],
                           graph_id: Optional[str] = None) -> "tuple[str, list[str], dict]":
        """Degraded (not hard-fail) if the simulation is hollow (0 organic posts/comments),
        errored, or truncated — the report must then NOT narrativize it as evidence."""
        import sqlite3
        issues: list[str] = []
        if not sim_id:
            return "ok", issues, {}
        base = self._sim_dir(sim_id)
        db_rows = 0
        db_found = False
        for dbn in ("reddit_simulation.db", "twitter_simulation.db"):
            p = os.path.join(base, dbn)
            if not os.path.exists(p):
                continue
            db_found = True
            try:
                c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                cur = c.cursor()
                for t in ("post", "comment"):
                    try:
                        db_rows += cur.execute(f"select count(*) from {t}").fetchone()[0]
                    except sqlite3.Error:
                        pass
                c.close()
            except sqlite3.Error:
                pass
        # XRUN-11: run_summary.json 已做诚实的种子/有机拆分（CREATE_POST/COMMENT − seeds），
        # 与 sqlite 裸行数（含种子+转发行）是两套口径、曾对同一 sim 给出 20 vs 40 的双重真相。
        # 存在时以 run_summary 为准；sqlite 计数保留为回退并改名 db_post_comment_rows。
        organic = db_rows
        organic_source = "db_rows"
        summary_health = None
        try:
            _sum_path = os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "run_summary.json")
            if os.path.exists(_sum_path):
                with open(_sum_path, encoding="utf-8") as _sf:
                    _summary = json.load(_sf)
                if isinstance(_summary, dict):
                    _oc = _summary.get("organic_action_count")
                    if isinstance(_oc, int):
                        organic = _oc
                        organic_source = "run_summary"
                    _sh = _summary.get("simulation_health")
                    if isinstance(_sh, str) and _sh:
                        summary_health = _sh
        except Exception:  # noqa: BLE001 — 老 run 无 summary → 沿用 db 口径
            pass
        err = None
        truncated = False
        rsp = os.path.join(base, "run_state.json")
        if os.path.exists(rsp):
            try:
                rs = json.load(open(rsp, encoding="utf-8"))
                err = rs.get("error")
                cr = rs.get("current_round")
                tr = rs.get("total_rounds") or rs.get("total_simulation_rounds")
                if isinstance(cr, int) and isinstance(tr, int) and tr > 0 and cr < tr:
                    truncated = True
            except (OSError, ValueError):
                pass
        # XRUN-6: 图谱反馈死信队列非空 = 报告将读到一张 episode 饥饿的图谱。计数暴露 + 降级，
        # 运维用 backend/scripts/replay_zep_dead_letters.py <graph_id> 重放（成功后归档、计数归零）。
        dead_letters = 0
        if graph_id:
            try:
                _dl_path = os.path.join(
                    Config.OASIS_SIMULATION_DATA_DIR, "_zep_dead_letter", f"{graph_id}.jsonl")
                if os.path.exists(_dl_path):
                    with open(_dl_path, encoding="utf-8") as _df:
                        dead_letters = sum(1 for _ln in _df if _ln.strip())
            except Exception:  # noqa: BLE001
                dead_letters = 0
        meta = {"organic_actions": organic, "organic_source": organic_source,
                "db_post_comment_rows": db_rows,
                "error": (str(err)[:160] if err else None),
                "truncated": truncated, "dead_letter_count": dead_letters}
        if summary_health:
            meta["simulation_health"] = summary_health
            if summary_health in ("hollow", "errored", "llm_degraded", "truncated"):
                issues.append(f"run_summary simulation_health={summary_health}")
        if err:
            issues.append(f"simulation error: {str(err)[:120]}")
        if truncated:
            issues.append("simulation was truncated before completion")
        if db_found and organic == 0:
            issues.append("simulation produced 0 organic posts/comments (hollow) — report must not cite it as evidence")
        if dead_letters:
            issues.append(
                f"{dead_letters} graph-feedback episode(s) in the dead-letter queue — "
                "report may read an episode-starved graph (replay via replay_zep_dead_letters.py)")
        health = "degraded" if issues else "ok"
        return health, issues, meta

    def _enforce_pipeline_health(self, state: PipelineState) -> None:
        """Aggregate stage health into state.options['pipeline_health']; HARD-FAIL (raise →
        status=failed) when the report deliverable is empty. Flag-gated + degrade-safe."""
        if not getattr(Config, "PIPELINE_HEALTH_GATE", True):
            return
        health: dict[str, Any] = {"status": "ok", "stages": {}}
        hard_issues: list[str] = []
        try:
            rh, ri, rm = self._assess_report_health(state.report_id)
            health["stages"]["report"] = {"health": rh, "issues": ri, **rm}
            if rh == "failed":
                hard_issues = ri
            if state.simulation_id:
                sh, si, sm = self._assess_run_health(state.simulation_id, graph_id=state.graph_id)
                health["stages"]["run"] = {"health": sh, "issues": si, **sm}
                if sh != "ok" and "run" in state.stages and not state.stages["run"].error:
                    state.stages["run"].error = "；".join(si) or None
            # KG-5/LOOP-003: 建图跳块或 destructive prune 未通过物理后置条件 → graph 降级。
            graph_issues: list[str] = []
            graph_meta: dict = {}
            _gskip = state.options.get("graph_ingest_degraded_ratio")
            if _gskip is not None:
                graph_issues.append(
                    f"graph build skipped {float(_gskip) * 100:.0f}% of chunks "
                    f"({state.options.get('graph_skipped_chunks')}/"
                    f"{state.options.get('graph_total_chunks')})"
                )
                graph_meta["skipped_ratio"] = _gskip
            _gprune = state.options.get("graph_prune") or {}
            if isinstance(_gprune, dict) and _gprune.get("degraded"):
                graph_issues.append(
                    str(_gprune.get("error") or _gprune.get("skipped_reason")
                        or "graph pruning hard-cap postcondition was not satisfied")
                )
                graph_meta["prune"] = _gprune
            if graph_issues:
                health["stages"]["graph"] = {
                    "health": "degraded",
                    "issues": graph_issues,
                    **graph_meta,
                }
            degraded = any(s.get("health") in ("degraded", "failed")
                           for s in health["stages"].values())
            health["status"] = "failed" if hard_issues else ("degraded" if degraded else "ok")
            state.options["pipeline_health"] = health
        except Exception as _he:  # noqa: BLE001 — assessment must never itself crash the run
            logger.warning("[%s] 健康评估异常（忽略）: %s", state.pipeline_id, _he)
            return
        if hard_issues:
            raise RuntimeError("交付物健康检查失败（deliverable is broken）: " + "；".join(hard_issues))
        if health["status"] == "degraded":
            logger.warning("[%s] 管线健康降级: %s", state.pipeline_id,
                           json.dumps(health["stages"], ensure_ascii=False)[:400])

    @staticmethod
    def _stage_artifact_specs(state: PipelineState, stage: str) -> list[tuple[str, str]]:
        """T6.3/I-4-3/I-4-6: 单一真源——某阶段「可深链产物」的 (name, 绝对路径) 候选列表。

        完成时登记（_record_stage_artifacts）、复用前完整性校验（_validate_reuse）、
        运行中临时产物发现（_discover_partial_artifacts）三处共用，避免三套各写一份文件名清单
        造成漂移。仅返回候选路径，不检查存在性（调用方按需过滤）。
        """
        hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
        specs: list[tuple[str, str]] = []
        if stage == STAGE_RESEARCH:
            specs.append(("report", os.path.join(hd, "research_report.md")))
            specs.append(("dossier", os.path.join(hd, "actors.json")))
            specs.append(("timeline", os.path.join(hd, "timeline.json")))
            specs.append(("sources", os.path.join(hd, "sources.json")))
            specs.extend(PipelineOrchestrator._viz_artifact_specs(hd))
        elif stage == STAGE_ONTOLOGY:
            specs.append(("ontology", os.path.join(hd, "ontology.json")))
        elif stage == STAGE_GRAPH:
            specs.append(("communities", os.path.join(hd, "communities.json")))
        elif stage == STAGE_PREPARE:
            sim_id = state.simulation_id
            if sim_id:
                sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, sim_id)
                specs.append(("initial_posts", os.path.join(sim_dir, "simulation_config.json")))
                for fname in ("twitter_profiles.csv", "reddit_profiles.json"):
                    specs.append(("personas", os.path.join(sim_dir, fname)))
        elif stage == STAGE_RUN:
            sim_id = state.simulation_id
            if sim_id:
                specs.append(("run_summary",
                              os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id, "run_summary.json")))
        elif stage == STAGE_REPORT:
            # 报告章节逐节落盘（section_NN.md）；运行中可作临时产物深链（见 _discover_partial_artifacts）。
            if state.report_id and bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True)):
                report_dir = ReportManager._get_report_folder(state.report_id)
                specs.append(("report_viz_manifest",
                              os.path.join(report_dir, "viz_manifest.json")))
        return specs

    @staticmethod
    def _charts_manifest_path(hd: str) -> str:
        """Return the producer-aligned charts manifest, with legacy fallback.

        The deterministic research renderer writes ``handoff/charts.json`` and
        keeps rendered assets under ``handoff/charts/``. Older experimental
        producers wrote ``handoff/charts/charts.json``; accept that layout only
        when the canonical file is absent.
        """
        canonical = os.path.join(hd, "charts.json")
        legacy = os.path.join(hd, "charts", "charts.json")
        if os.path.exists(canonical) or not os.path.exists(legacy):
            return canonical
        return legacy

    @staticmethod
    def _viz_artifact_specs(hd: str) -> list[tuple[str, str]]:
        """VIZ-2: 可视化产物通道的「索引文件」候选 (name, 绝对路径)——供 RESEARCH/REPORT 两阶段共用。

        单文件锚点（与 charts/data 目录内动态命名的 png|svg|csv 一一对应地登记不现实，故以索引清单
        为完整性/深链锚）：
          - charts    → handoff/charts.json（图表清单；兼容旧 handoff/charts/charts.json）
          - datasets  → handoff/data/datasets.json（数据集清单，与 charts.json 平行）
        二者均为可选、且在旧跑上不存在：调用方（登记/复用校验/临时产物发现）一律按「文件缺失即跳过」
        自然降级，绝不误伤健康/完整性校验。原始 *.png|svg / *.csv 作为增量证据另经
        _discover_partial_artifacts 目录扫描透出（见该方法）。关闭 PIPELINE_VIZ_ARTIFACTS 时整段返回空。
        """
        if not bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True)):
            return []
        return [
            ("charts", PipelineOrchestrator._charts_manifest_path(hd)),
            ("datasets", os.path.join(hd, "data", "datasets.json")),
        ]

    @staticmethod
    def _viz_dynamic_artifact_specs(hd: str) -> list[tuple[str, str]]:
        """Enumerate bounded chart assets under one artifact root for deep links.

        These files appear only after the final deterministic research-chart
        step, so completion-time registration must not depend on an earlier
        progress-poll scan. Symlinks and unsupported files are excluded.
        """
        charts_dir = os.path.join(hd, "charts")
        if not os.path.isdir(charts_dir):
            return []
        allowed = {".png", ".svg", ".jpg", ".jpeg", ".webp", ".html"}
        out: list[tuple[str, str]] = []
        try:
            for filename in sorted(os.listdir(charts_dir)):
                path = os.path.join(charts_dir, filename)
                if (os.path.splitext(filename)[1].lower() not in allowed
                        or os.path.islink(path) or not os.path.isfile(path)):
                    continue
                out.append((f"chart_{filename}", path))
        except OSError:
            return []
        return out

    @staticmethod
    def _report_viz_dynamic_artifact_specs(report_dir: str) -> list[tuple[str, str]]:
        """Report-owned chart assets, namespaced away from research chart keys."""
        return [
            (f"report_{name}", path)
            for name, path in PipelineOrchestrator._viz_dynamic_artifact_specs(report_dir)
        ]

    def _record_stage_artifacts(self, state: PipelineState, stage: str) -> None:
        """T6.3: 阶段完成时登记其产物的可深链路径（存在且非空才登记）。

        I-4-3: 同时把每条登记的产物写入 handoff/manifest.json（sha256/字节/产出阶段/schema_ok），
        供后续 resume 复用前做完整性校验（_validate_reuse）。清单写失败仅退化为「无清单」，
        不影响 T6.3 的深链登记。
        """
        recorded: list[tuple[str, str]] = []

        def add_if(name: str, path: Optional[str]) -> bool:
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                state.artifacts[name] = path
                recorded.append((name, path))
                return True
            return False

        if stage == STAGE_PREPARE:
            # personas 取首个存在者（twitter_profiles.csv | reddit_profiles.json）。
            for name, path in self._stage_artifact_specs(state, stage):
                if name == "personas":
                    if add_if("personas", path):
                        break
                else:
                    add_if(name, path)
        else:
            for name, path in self._stage_artifact_specs(state, stage):
                add_if(name, path)

        # Research charts are created at the very end of the subprocess. Register
        # them again at the completion boundary so deep links never depend on a
        # lucky progress-poll scan occurring after rendering.
        if stage == STAGE_RESEARCH and bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True)):
            hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
            for name, path in self._viz_dynamic_artifact_specs(hd):
                add_if(name, path)
        elif (stage == STAGE_REPORT and state.report_id
              and bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True))):
            report_dir = ReportManager._get_report_folder(state.report_id)
            for name, path in self._report_viz_dynamic_artifact_specs(report_dir):
                add_if(name, path)

        # I-4-3: 把本阶段实际登记到的产物写入完整性清单。
        if recorded and bool(getattr(Config, "PIPELINE_VALIDATE_ARTIFACTS", True)):
            try:
                manifest = PipelineManager.load_artifact_manifest(state.pipeline_id)
                for name, path in recorded:
                    entry = _manifest_entry_for(name, path, stage)
                    if entry is not None:
                        manifest[name] = entry
                PipelineManager.write_artifact_manifest(state.pipeline_id, manifest)
            except Exception as e:  # noqa: BLE001 — 清单是复用保障，写失败仅退化为无校验
                logger.debug("[%s] 产物清单写出跳过: %s", state.pipeline_id, e)

    def _fail_stage(self, state: PipelineState, stage: str, error: str):
        st = state.stages.setdefault(stage, StageState(name=stage))
        st.status = "failed"
        st.error = error
        st.finished_at = _utcnow()
        PipelineManager.save(state)
        # W9-3：失败也是阶段转换——立即落一版遥测账（失败跑的花费不再消失）。
        self._flush_run_telemetry(state)

    # -- 内部：产物完整性校验（复用前守卫） (I-4-3) ------------------------

    def _validate_reuse(self, state: PipelineState, stage: str) -> bool:
        """I-4-3: 复用一个标记 completed 的阶段前，按产物清单核验其产物未被半写/截断/篡改。

        对该阶段在清单里登记过的每条产物：重算 sha256 + 字节数与清单比对，并跑轻量 schema 探针。
        任一不符 → 返回 False（调用方应回落到重建并留痕），全部通过 → True。

        语义边界（保守、绝不误杀一次本可复用的产物）：
          - 清单整体缺失（老管线 / 关闭过校验）→ True（按旧的「存在性复用」行为放行）；
          - 某产物在清单里没有条目（阶段产出可选，如无 communities.json）→ 不校验该产物；
          - 清单里有条目但磁盘文件已不存在 → False（产物丢了，必须重建）；
          - sha256 当时未能计算（None）→ 仅比字节数 + schema（无哈希可比时不因此误判）。
        仅在 Config.PIPELINE_VALIDATE_ARTIFACTS 开启时被调用（见各复用分支）。
        """
        manifest = PipelineManager.load_artifact_manifest(state.pipeline_id)
        if not manifest:
            return True  # 无清单 → 退化到旧的存在性复用，保持向后兼容
        ok = True
        for name, path in self._stage_artifact_specs(state, stage):
            entry = manifest.get(name)
            if not isinstance(entry, dict):
                continue  # 该产物未登记（可选 / 老清单）→ 不校验
            man_path = entry.get("path") or path
            if not os.path.exists(man_path):
                logger.warning("[%s] 复用校验：产物 %s 缺失（%s）", state.pipeline_id, name, man_path)
                ok = False
                break
            try:
                size = os.path.getsize(man_path)
            except OSError:
                ok = False
                break
            exp_bytes = entry.get("bytes")
            if isinstance(exp_bytes, int) and exp_bytes != size:
                logger.warning("[%s] 复用校验：产物 %s 字节数变更 %s→%s",
                               state.pipeline_id, name, exp_bytes, size)
                ok = False
                break
            exp_sha = entry.get("sha256")
            if isinstance(exp_sha, str) and exp_sha:
                cur_sha = _sha256_file(man_path)
                if cur_sha is not None and cur_sha != exp_sha:
                    logger.warning("[%s] 复用校验：产物 %s 哈希不符（内容变更/损坏）",
                                   state.pipeline_id, name)
                    ok = False
                    break
            if not _probe_artifact_schema(name, man_path):
                logger.warning("[%s] 复用校验：产物 %s 结构探针未通过", state.pipeline_id, name)
                ok = False
                break
        return ok

    def _reuse_ok(self, state: PipelineState, stage: str) -> bool:
        """I-4-3: 复用守卫的薄封装——校验关闭时恒 True，开启时调 _validate_reuse。

        各阶段（GRAPH/PREPARE/RUN）的复用分支统一经此判定，校验失败时由调用方
        把对应阶段降级重建，并写 resumed_stage_validation 面包屑（与既有图谱重建路径同构）。
        """
        if not bool(getattr(Config, "PIPELINE_VALIDATE_ARTIFACTS", True)):
            return True
        try:
            return self._validate_reuse(state, stage)
        except Exception as e:  # noqa: BLE001 — 校验自身异常绝不阻断一次合法复用
            logger.debug("[%s] 复用校验异常（放行）: %s", state.pipeline_id, e)
            return True

    # -- 内部：运行中临时产物深链 (I-4-6) ----------------------------------

    @staticmethod
    def _discover_partial_artifacts(state: PipelineState, stage: str) -> list[dict[str, Any]]:
        """I-4-6: best-effort 发现某阶段「此刻已落盘」的临时产物（含字节/mtime，标记 provisional）。

        长且大多无声的阶段（深度研究、多轮模拟、多章节报告）正是用户最需要增量证据来
        判断是否取消的地方。本方法只读目录元信息（cheap），不解析内容；返回的每条都带
        provisional 标记，供 UI/API 永不把临时产物当成最终产物。
        REPORT 额外扫描 section_NN.md（逐节落盘），RUN 额外扫描尚未聚合的 actions/run-state。
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _stat(name: str, path: str) -> None:
            if not path or path in seen or not os.path.exists(path):
                return
            try:
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                return
            if size <= 0:
                return
            seen.add(path)
            out.append({
                "name": name,
                "path": path,
                "bytes": size,
                "mtime": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "provisional": True,
            })

        # 1) 阶段已知产物（与完成时登记同源，但此处不要求阶段已完成）。
        for name, path in PipelineOrchestrator._stage_artifact_specs(state, stage):
            _stat(name, path)

        # 2) 阶段特有的「过程中产物」（完成时清单未涵盖的逐步落盘文件）。
        try:
            if stage == STAGE_RESEARCH:
                hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                _stat("research_report", os.path.join(hd, "research_report.md"))
                _stat("research_progress", os.path.join(hd, "research_progress.log"))
            elif stage == STAGE_RUN:
                sim_id = state.simulation_id
                if sim_id:
                    rsd = os.path.join(SimulationRunner.RUN_STATE_DIR, sim_id)
                    _stat("run_state", os.path.join(rsd, "run_state.json"))
                    # actions.jsonl 按平台分目录（twitter/ reddit/）逐行 append，运行中即可观测。
                    for _plat in ("twitter", "reddit"):
                        _stat(f"actions_{_plat}", os.path.join(rsd, _plat, "actions.jsonl"))
            elif stage == STAGE_REPORT:
                # 报告章节逐节写入 ReportManager 的报告目录；扫描 section_*.md 排序后透出。
                rid = state.report_id
                if rid:
                    try:
                        rep_dir = ReportManager._get_report_folder(rid)
                    except Exception:  # noqa: BLE001
                        rep_dir = None
                    if rep_dir and os.path.isdir(rep_dir):
                        for fn in sorted(os.listdir(rep_dir)):
                            if fn.startswith("section_") and fn.endswith(".md"):
                                _stat(fn[:-3], os.path.join(rep_dir, fn))
            # VIZ-2: each stage scans its own visual root. Research assets live in
            # handoff/charts; final-report assets live in reports/<id>/charts.
            if stage == STAGE_RESEARCH and bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True)):
                hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                for _name, _path in PipelineOrchestrator._viz_dynamic_artifact_specs(hd):
                    _stat(_name, _path)
                _ddir = os.path.join(hd, "data")
                if os.path.isdir(_ddir):
                    for fn in sorted(os.listdir(_ddir)):
                        if fn.endswith(".csv"):
                            _stat(f"dataset_{fn}", os.path.join(_ddir, fn))
            elif (stage == STAGE_REPORT and state.report_id
                  and bool(getattr(Config, "PIPELINE_VIZ_ARTIFACTS", True))):
                _report_dir = ReportManager._get_report_folder(state.report_id)
                for _name, _path in PipelineOrchestrator._report_viz_dynamic_artifact_specs(
                        _report_dir):
                    _stat(_name, _path)
        except Exception:  # noqa: BLE001 — 发现是观测增益，永不抛出
            pass
        return out

    def _register_partial_artifacts(self, state: PipelineState, stage: str) -> None:
        """I-4-6: 运行中把新出现的临时产物以 provisional 键登记进 state.artifacts（节流、不覆盖正式产物）。

        节流：同一阶段每隔 PIPELINE_PARTIAL_SCAN_EVERY_S 秒最多扫一次磁盘（默认 10s），
        避免在每次进度回调（可能秒级多次）都做目录 stat。登记键形如 'report_partial'，与完成时
        登记的正式键（'run_summary' 等）分离，确保完成产物永不被临时产物覆盖。
        关闭 PIPELINE_LIVE_ARTIFACTS 时整段 no-op（行为回到只在完成时登记）。
        """
        if not bool(getattr(Config, "PIPELINE_LIVE_ARTIFACTS", True)):
            return
        now = time.time()
        every = float(getattr(Config, "PIPELINE_PARTIAL_SCAN_EVERY_S", 10) or 10)
        # 节流戳存在 orchestrator 实例（每次 _run 新建一个实例，更新器闭包持有它），
        # 仅在本次运行内跨调用有效——刻意不落到 state.options，避免污染持久化状态文件。
        throttle = self._partial_scan_at
        last = throttle.get(stage)
        if isinstance(last, (int, float)) and (now - last) < every:
            return
        throttle[stage] = now
        try:
            partials = self._discover_partial_artifacts(state, stage)
        except Exception:  # noqa: BLE001
            return
        for p in partials:
            name = p.get("name")
            path = p.get("path")
            if not name or not path:
                continue
            key = f"{name}_partial"
            # 正式产物（同名非 _partial 键）已登记则不再重复打临时键，避免冗余。
            if name in state.artifacts:
                continue
            state.artifacts[key] = path

    # -- 内部：ETA / spend 心跳估计 (I-5-6) --------------------------------

    def estimate_eta(self, state: PipelineState) -> dict[str, Any]:
        """I-5-6: 从已有数据廉价推导 elapsed / 近似 ETA / staleness（不发任何请求）。

        ETA 用「当前 global_progress 占比 vs 自 created_at 起的已耗时」线性外推剩余时间——刻意标注
        approximate（管线各阶段速率差异大，仅作量级参考）。stale 判定：自 last_progress_at（无则
        updated_at）起超过 PIPELINE_STALE_S 秒未更新 → True（UI 显示「仍在思考」而非「卡死」）。
        终态（completed/failed/cancelled）下 eta=0、不再 stale。供状态 API 增量拼进响应（纯附加字段）。
        """
        out: dict[str, Any] = {
            "elapsed_s": None,
            "eta_s": None,
            "eta_approximate": True,
            "stale": False,
            "last_progress_age_s": None,
        }
        # ORCH-7: 恢复过的管线以 resumed_at 为耗时锚——created_at 可能是数天前，用它线性外推
        # 会立刻顶到 PIPELINE_ETA_CAP_S（resume_count=5 的管线每个会话 ETA 全是噪声）。
        _anchor = state.options.get("resumed_at") if isinstance(state.options, dict) else None
        elapsed = _age_seconds(_anchor) if _anchor else None
        if elapsed is None:
            elapsed = _age_seconds(state.created_at)
        if elapsed is not None:
            out["elapsed_s"] = int(elapsed)
        status = state.status
        terminal = status in ("completed", "failed", "cancelled")
        # staleness：仅对在飞管线有意义。
        ref = state.last_progress_at or state.updated_at
        age = _age_seconds(ref)
        if age is not None:
            out["last_progress_age_s"] = int(age)
        if not terminal and age is not None:
            stale_s = float(getattr(Config, "PIPELINE_STALE_S", 300) or 300)
            out["stale"] = age > stale_s
        # ETA：线性外推（progress→剩余时间）。需要正进度与正耗时。
        if terminal:
            out["eta_s"] = 0
        else:
            prog = max(0, min(100, int(state.global_progress or 0)))
            if elapsed is not None and elapsed > 0 and 0 < prog < 100:
                remaining = elapsed * (100 - prog) / prog
                # 夹取到合理上限，避免极早期（prog=1）外推出离谱数字误导用户。
                cap = float(getattr(Config, "PIPELINE_ETA_CAP_S", 7200) or 7200)
                out["eta_s"] = int(min(remaining, cap))
        return out

    def heartbeat_status(self, state: PipelineState) -> dict[str, Any]:
        """I-4-1/I-5-6: 汇总 ETA/staleness 心跳 + （计量开启时）当前累计花费，供状态 API 透出。

        spend_so_far 来自 LLMMeter.snapshot（进程内累计，仅对本进程在飞的管线有数据；
        重启后或别的 worker 的管线读不到 → 省略该字段，不臆造）。整体 best-effort。
        """
        info = self.estimate_eta(state)
        # owner/心跳液体性（reconcile 也用同一判据，这里只读不改）。
        info["owner_pid"] = state.owner_pid
        info["owner_alive"] = _pid_alive(state.owner_pid) if state.owner_pid else None
        hb_age = _age_seconds(state.heartbeat_at)
        info["heartbeat_age_s"] = int(hb_age) if hb_age is not None else None
        if bool(getattr(Config, "LLM_TELEMETRY_ENABLED", True)):
            try:
                from ..utils.telemetry import LLMMeter
                snap = LLMMeter.snapshot(state.pipeline_id)
                tot = snap.get("total") or {}
                # 仅在确有计量数据时透出（全 0 视为「本进程无数据」省略）。
                if tot.get("total_tokens") or tot.get("cost_usd"):
                    info["spend_so_far"] = {
                        "tokens": tot.get("total_tokens"),
                        "cost_usd": tot.get("cost_usd"),
                    }
            except Exception:  # noqa: BLE001
                pass
        return info

    # -- 内部：心跳看护线程 (I-4-1) ----------------------------------------

    def _start_heartbeat(self, state: PipelineState) -> Optional[threading.Event]:
        """I-4-1: 钉入 owner 指纹并启动壁钟心跳看护线程。返回用于在 finally 停止它的 Event。

        看护线程独立于阶段进度，每 PIPELINE_HEARTBEAT_INTERVAL_S 秒原子刷新一次 heartbeat_at
        （走 touch_heartbeat 轻量直写，不触发 full save）。即便研究子进程/persona 生成长时间无
        进度回调，心跳仍在跳，让别的进程的 reconcile_orphans 看到「这条管线还活着」。
        关闭 PIPELINE_HEARTBEAT_ENABLED 时不启动线程、不写 owner（回退旧的 _threads 判定），返回 None。
        """
        if not bool(getattr(Config, "PIPELINE_HEARTBEAT_ENABLED", True)):
            return None
        # 钉入 owner 指纹 + 首拍心跳（即便随后看护线程才起，状态文件也已带 owner/心跳）。
        try:
            state.owner_pid = os.getpid()
            state.owner_boot_id = _BOOT_ID
            state.heartbeat_at = _utcnow()
        except Exception:  # noqa: BLE001
            pass
        interval = float(getattr(Config, "PIPELINE_HEARTBEAT_INTERVAL_S", 30) or 30)
        stop = threading.Event()
        pid = os.getpid()

        def _beat() -> None:
            # 立即落一拍，再按节律续跳，直到 _run 在 finally 置位 stop。
            while not stop.wait(interval):
                try:
                    if not PipelineManager.touch_heartbeat(state.pipeline_id, pid=pid):
                        # 状态已非 running（终态）→ 无需再跳，提前退出看护线程。
                        return
                except Exception as e:  # noqa: BLE001 — 心跳失败不得拖垮管线
                    logger.debug("[%s] 心跳刷新跳过: %s", state.pipeline_id, e)
                # W9-3：心跳节律上的遥测 flush 检查点——覆盖长时间无进度回调的窗口
                # （集成种子/深研究静默期），LLM 调用每新增 N 次都会被落盘。
                try:
                    self._maybe_flush_run_telemetry(state)
                except Exception:  # noqa: BLE001
                    pass

        t = threading.Thread(target=_beat, name=f"pipeline-hb-{state.pipeline_id}", daemon=True)
        t.start()
        return stop

    # -- 内部：可复现性清单 run.json (I-8-1) -------------------------------

    def _write_run_manifest(self, state: PipelineState) -> None:
        """I-8-1: 在管线启动时写出首版 run.json（best-effort，绝不阻断运行）。

        与 _persist_env 一样整体包 try/except：清单写失败只是少了审计产物，
        不能让它拖垮一次真正的预测运行。RECORD_RUN_MANIFEST=false 时整段跳过。
        """
        if not bool(getattr(Config, "RECORD_RUN_MANIFEST", True)):
            return
        try:
            from ..utils.security import redact_secrets
            from ..utils.atomic import write_json_atomic
            manifest = redact_secrets(_build_run_manifest(state))
            write_json_atomic(PipelineManager.manifest_path(state.pipeline_id), manifest)
            # 登记可深链指针，供 StageTimeline / GET /manifest 复用。
            if isinstance(state.artifacts, dict):
                state.artifacts["run_manifest"] = PipelineManager.manifest_path(state.pipeline_id)
        except Exception as e:  # noqa: BLE001 — 清单是观测产物，写失败必须静默降级
            logger.debug("[%s] run.json 写出跳过: %s", state.pipeline_id, e)

    def _update_manifest(self, state: PipelineState, stage: str,
                         total_rounds: Optional[int] = None) -> None:
        """I-8-1: 阶段进入时把当前解析出的 provider/model（可热切换）钉入 run.json。

        读出现有清单，更新对应 stage 的 resolved.provider/model_name（ontology/graph/report）
        与 simulation.total_rounds，原子写回。任何异常静默吞掉。
        """
        if not bool(getattr(Config, "RECORD_RUN_MANIFEST", True)):
            return
        try:
            from ..utils.security import redact_secrets
            from ..utils.atomic import write_json_atomic
            path = PipelineManager.manifest_path(state.pipeline_id)
            manifest = _read_json(path)
            if not isinstance(manifest, dict):
                # 清单缺失（如老管线 resume）：重建首版骨架。
                manifest = redact_secrets(_build_run_manifest(state))
            resolved = manifest.setdefault("resolved", {})
            if stage in (STAGE_ONTOLOGY, STAGE_GRAPH, STAGE_REPORT):
                resolved[stage] = _current_provider_pair()
            if total_rounds is not None:
                sim = resolved.setdefault("simulation", {})
                sim["total_rounds"] = int(total_rounds)
            manifest["updated_at"] = _utcnow()
            write_json_atomic(path, redact_secrets(manifest))
        except Exception as e:  # noqa: BLE001
            logger.debug("[%s] run.json 更新跳过: %s", state.pipeline_id, e)

    # -- 内部：研究阶段遥测 (I-5-7) ----------------------------------------

    def _record_research_telemetry(self, state: PipelineState,
                                   telemetry: Optional[dict[str, Any]]) -> None:
        """I-5-7: 把 DeerFlowResearchRunner 返回的研究遥测纳入统一计量。

        (1) 始终 stash 到 state.options['research_telemetry']（即使计量关闭，也是免费观测）；
        (2) 计量开启且确有 token 时，向 LLMMeter 写一条 stage='research' 的合成记录，
            让 run_telemetry.json 的整轮 token/成本 rollup 把最贵的研究阶段也算进去。
        resume 路径无遥测（telemetry=None）→ 直接跳过，保持旧行为。
        """
        if not isinstance(telemetry, dict):
            return
        try:
            state.options["research_telemetry"] = telemetry
            PipelineManager.save(state)
        except Exception:  # noqa: BLE001 — stash 失败不影响主流程
            pass
        if not bool(getattr(Config, "LLM_TELEMETRY_ENABLED", True)):
            return
        t_in = int(telemetry.get("tokens_in") or 0)
        t_out = int(telemetry.get("tokens_out") or 0)
        if t_in <= 0 and t_out <= 0:
            return  # 该研究模型未报 usage → 无可计量 token，跳过合成记录
        try:
            from ..utils.telemetry import LLMMeter
            model = str(telemetry.get("model") or getattr(Config, "DEERFLOW_MODEL", "claude"))
            # 研究模型名映射到一个计价 provider 键：CLI 订阅类（claude/codex）边际成本为 0，
            # 其余复用同名 provider 的定价表（缺失则成本 0，仍记 token/延迟）。
            provider = "claude-cli" if model in ("claude", "codex") else model
            wall_ms = float(telemetry.get("wall_s") or 0.0) * 1000.0
            LLMMeter.record(
                provider=provider,
                model=model,
                prompt_tokens=t_in,
                completion_tokens=t_out,
                latency_ms=wall_ms,
                stage=STAGE_RESEARCH,
                run_id=state.pipeline_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[%s] 研究阶段合成计量跳过: %s", state.pipeline_id, e)

    # -- 内部：研究覆盖度/质量记分牌 (I-0-3) -------------------------------

    def _surface_research_quality(self, state: PipelineState, handoff_dir: str) -> None:
        """I-0-3: 把研究阶段写出的 research_quality 记分牌（来自 handoff/meta.json）
        透传到 state.options，供 StageTimeline / API 展示。

        记分牌由 DeerFlow bridge 的 compute_research_quality() 写入 meta.json（跨文件部分）。
        本方法只负责「读出并上抛」：若 bridge 尚未写该字段（旧 bridge / 未启用），静默 no-op，
        保持现有行为不变（纯观测、零成本、永不硬失败）。

        若 RESEARCH_QUALITY_GATE 开启且记分牌的 score 低于 RESEARCH_QUALITY_FLOOR，
        在 options 里打一个软告警标记（real gap-pass 由 bridge 在研究阶段内自校正）。
        """
        try:
            meta = _read_json(os.path.join(handoff_dir, "meta.json"))
        except Exception:  # noqa: BLE001
            meta = None
        if not isinstance(meta, dict):
            return
        rq = meta.get("research_quality")
        if not isinstance(rq, dict):
            return
        try:
            state.options["research_quality"] = rq
            # 软告警：记分牌偏低时留痕（不阻断管线——GIGO 是软信号，宁可继续也不误杀小盘口事件）。
            gate_on = bool(getattr(Config, "RESEARCH_QUALITY_GATE", False))
            floor = float(getattr(Config, "RESEARCH_QUALITY_FLOOR", 0.0) or 0.0)
            score = rq.get("score")
            if gate_on and isinstance(score, (int, float)) and floor > 0 and score < floor:
                state.options["research_quality_warning"] = (
                    f"research quality score {round(float(score), 3)} < floor {floor}"
                )
                logger.warning(
                    "[%s] 研究质量记分牌偏低: score=%.3f < floor=%.3f（继续，不硬失败）",
                    state.pipeline_id, float(score), floor,
                )
            PipelineManager.save(state)
        except Exception as e:  # noqa: BLE001
            logger.debug("[%s] research_quality 透传跳过: %s", state.pipeline_id, e)

    # -- 内部：预测信心罚分 (R2-RES-3) -------------------------------------

    @staticmethod
    def _compute_forecast_confidence_penalty(
        meta: Optional[dict], dossier_coverage: Optional[dict]
    ) -> tuple[float, dict]:
        """R2-RES-3 (pure): derive an advisory forecast-confidence penalty in [0, 0.3]
        from research evidence quality.

        Three additive sources, each capped so the total is a soft demotion signal and
        never large enough to be load-bearing on its own:
          * research_quality.score below RESEARCH_QUALITY_FLOOR (proportional, ≤0.15);
          * source-tier mix skewed to low-tier sources (≤~0.08);
          * weak dossier-coverage signals (0.05 each, ≤0.10).
        Returns ``(penalty, components)``; never raises (caller wraps too)."""
        meta = meta if isinstance(meta, dict) else {}
        components: dict[str, float] = {}
        penalty = 0.0

        rq = meta.get("research_quality")
        if isinstance(rq, dict):
            score = rq.get("score")
            try:
                floor = float(getattr(Config, "RESEARCH_QUALITY_FLOOR", 0.0) or 0.0)
            except (TypeError, ValueError):
                floor = 0.0
            if isinstance(score, (int, float)) and floor > 0 and score < floor:
                c = round(min(0.15, float(floor) - float(score)), 3)
                if c > 0:
                    components["research_quality"] = c
                    penalty += c

        tiers = meta.get("source_tiers")
        if isinstance(tiers, dict) and tiers:
            _w = {"s1": 1.0, "s2": 0.7, "s3": 0.4}
            tot = 0
            wsum = 0.0
            for t, n in tiers.items():
                try:
                    cnt = int(n)
                except (TypeError, ValueError):
                    continue
                if cnt <= 0:
                    continue
                wsum += _w.get(str(t).strip().lower(), 0.2) * cnt
                tot += cnt
            if tot > 0:
                c = round(max(0.0, 1.0 - (wsum / tot)) * 0.1, 3)
                if c > 0:
                    components["source_tier_mix"] = c
                    penalty += c

        if isinstance(dossier_coverage, dict):
            weak = 0
            if dossier_coverage.get("n_actors", 0) and dossier_coverage.get("pct_actors_with_incentives", 0) < 0.34:
                weak += 1
            if dossier_coverage.get("n_relationships", 0) == 0:
                weak += 1
            elif dossier_coverage.get("n_relationships", 0) and dossier_coverage.get("pct_edges_valenced", 0) < 0.2:
                weak += 1
            if weak:
                c = round(min(0.10, 0.05 * weak), 3)
                components["dossier_coverage"] = c
                penalty += c

        return round(min(0.3, penalty), 3), components

    def _surface_forecast_confidence_penalty(self, state: PipelineState, handoff_dir: str) -> None:
        """R2-RES-3: stash an advisory ``forecast_confidence_penalty`` (+ component
        breakdown) into ``state.options`` for the publish gate to consume later.

        Pure observation: only *writes* the number — the publish gate that reads it
        (gate refine) is deliberately deferred to a later change, so nothing here can
        block or wedge a run. Best-effort; never raises."""
        try:
            meta = _read_json(os.path.join(handoff_dir, "meta.json"))
            penalty, components = self._compute_forecast_confidence_penalty(
                meta if isinstance(meta, dict) else None,
                state.options.get("dossier_coverage"),
            )
            state.options["forecast_confidence_penalty"] = penalty
            state.options["forecast_confidence_penalty_components"] = components
            if penalty > 0:
                logger.info("[%s] forecast_confidence_penalty=%.3f（来源: %s）",
                            state.pipeline_id, penalty, components)
            PipelineManager.save(state)
        except Exception as e:  # noqa: BLE001 — 罚分纯观测，失败不影响主流程
            logger.debug("[%s] forecast_confidence_penalty 计算跳过: %s", state.pipeline_id, e)

    # -- 内部：研究 as_of 锚校验 (R2-RES-7) -------------------------------

    @staticmethod
    def _validate_as_of_date(actors: Any, sources: Any) -> tuple[Optional[datetime], Optional[str]]:
        """R2-RES-7: validate ``actors.as_of_date`` — the bi-temporal anchor used as the
        seed ``valid_at`` and as every research chunk's ``reference_time``.

        A trustworthy anchor must (a) parse, (b) be no later than the run date (no
        future anchor), and (c) be no earlier than the newest source publication date
        (the anchor cannot predate the evidence it summarizes). On any violation we fall
        back to max(source dates) clamped to the run date, else the run date, and return
        a human-readable note for telemetry.

        Returns ``(as_of_dt | None, note | None)``. When ``as_of_date`` is simply absent
        AND there are no source dates, returns ``(None, None)`` to preserve today's
        "no anchor" behavior byte-for-byte. Never raises."""
        run_dt = datetime.now(timezone.utc)
        max_src: Optional[datetime] = None
        if isinstance(sources, list):
            for s in sources:
                if not isinstance(s, dict):
                    continue
                d = parse_as_of(s.get("date"))
                if d is not None and (max_src is None or d > max_src):
                    max_src = d
        raw = actors.get("as_of_date") if isinstance(actors, dict) else None
        parsed = parse_as_of(raw)
        note: Optional[str] = None
        if parsed is not None:
            if parsed > run_dt:
                note = f"as_of_date {parsed.date()} 晚于运行日 {run_dt.date()}，回退"
                parsed = None
            elif max_src is not None and parsed < max_src:
                note = f"as_of_date {parsed.date()} 早于最新来源日 {max_src.date()}，回退"
                parsed = None
        elif raw not in (None, ""):
            note = f"as_of_date 无法解析（{raw!r}），回退"
        if parsed is not None:
            return parsed, None
        if max_src is not None:
            return (max_src if max_src <= run_dt else run_dt), note
        if raw in (None, ""):
            # 既无 as_of_date 也无来源日 → 维持今日「无锚」行为（degrade-safe）。
            return None, None
        # as_of_date 存在但无效且无来源日可回退 → 运行日兜底（优于带脏日期入图）。
        return run_dt, note

    # -- 内部：PAR-2 多角度并行研究轨 ------------------------------------

    def _run_parallel_research_tracks(
        self, state: "PipelineState", handoff_dir: str,
        upd: "Callable[[int, str], None]", n_tracks: int,
    ) -> dict[str, Any]:
        """PAR-2：并行跑 K 条角度特化研究轨，确定性合并回 handoff_dir，返回 research 摘要。

        每轨 = 一个 DeerFlowResearchRunner 子进程（角度前缀 + 原始 prompt），写入
        handoff/track_<k>/。各轨用既有 per-run 看门狗预算 → wall-clock ≈ 1 轨。某轨失败仅
        跳过（存活 ≥1 即继续并打降级标）；全失败 → RuntimeError（阶段失败，与今日一致）。
        取消（cancel_event）穿透所有轨并向上重抛。返回 dict 的键与 DeerFlowResearchRunner.run
        一致（report/report_path/actor_dossier/actors/sources/timeline/exit_code/research_telemetry），
        供下游各阶段无差别消费。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..utils.atomic import write_json_atomic, write_text_atomic

        cls = type(self)
        angles = _RESEARCH_TRACK_ANGLES[:max(1, n_tracks)]
        n = len(angles)
        os.makedirs(handoff_dir, exist_ok=True)
        # PAR-2：先在启动任何轨之前把 bridge→deployed 同步做一次。每个 .run 内部也会调
        # _sync_deerflow_bridge_if_stale，但 K 条轨并发时它们会同时 shutil.copyfile 同一组
        # 文件（非原子）→ 竞态可能让某轨读到半写的脚本。预同步后各轨内的同步退化为只读哈希
        # 比对（no-op），消除该竞态。失败仅记录（同函数语义，绝不阻断研究）。
        try:
            _sync_deerflow_bridge_if_stale(Config.DEERFLOW_DIR)
        except Exception:  # noqa: BLE001
            pass
        upd(2, f"并行研究：启动 {n} 条多角度研究轨…")

        # 进度聚合：取各轨 local 的最小值作为阶段进度，让进度条随「最慢轨」诚实推进
        # （wall-clock ≈ 1 轨）。upd 自身也会在取消时抛 PipelineCancelled。
        prog_lock = threading.Lock()
        track_local = [0] * n
        pids_lock = threading.Lock()

        def _make_cb(idx: int) -> "Callable[[int, str], None]":
            def _cb(local: int, msg: str) -> None:
                ev = cls._cancel_events.get(state.pipeline_id)
                if ev is not None and ev.is_set():
                    raise PipelineCancelled("并行研究期间被取消")
                with prog_lock:
                    track_local[idx] = int(local)
                    agg = min(track_local)
                upd(max(2, agg), f"[轨{idx + 1}] {_tail(msg)}")
            return _cb

        def _make_spawn(idx: int) -> "Callable[[int], None]":
            def _spawn(pid: int) -> None:
                # 持久化各轨 PID，供后端重启时 reconcile_orphans 逐一清理孤儿研究子进程。
                # 单一 state.research_pid 字段保留首个 PID（back-compat）；完整清单入 options。
                with pids_lock:
                    lst = state.options.setdefault("research_track_pids", [])
                    if int(pid) not in lst:
                        lst.append(int(pid))
                    if state.research_pid is None:
                        state.research_pid = int(pid)
                PipelineManager.save(state)
            return _spawn

        def _run_track(idx: int, angle_title: str, prefix: str) -> tuple:
            track_dir = os.path.join(handoff_dir, f"track_{idx + 1}")
            os.makedirs(track_dir, exist_ok=True)
            track_prompt = (prefix + state.prompt) if prefix else state.prompt
            # ITEM-3：每轨各写自己的 track_<k>/research_checkpoint.json；重试时逐轨判定续跑
            # （某轨已完成的 pass 不重跑，只补未完成的）。首跑无 checkpoint → 全量跑该轨。
            _resume_track = _has_research_checkpoint(track_dir)
            res = DeerFlowResearchRunner.run(
                track_prompt,
                track_dir,
                on_progress=_make_cb(idx),
                depth=state.options.get("depth"),
                language=state.options.get("research_language"),
                model=state.options.get("research_model"),
                cancel_event=cls._cancel_events.get(state.pipeline_id),
                on_spawn=_make_spawn(idx),
                resume=_resume_track,
                kg_graph_id=state.graph_id,  # W9-9：图谱已存在时接入 KG MCP（首跑为 None → no-op）
            )
            return idx, angle_title, track_dir, res

        results: dict[int, tuple] = {}
        cancelled: Optional[PipelineCancelled] = None
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="research-track") as ex:
            futs = {ex.submit(_run_track, i, angles[i][1], angles[i][2]): i for i in range(n)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    idx, angle_title, track_dir, res = fut.result()
                    results[idx] = (angle_title, track_dir, res)
                except PipelineCancelled as pc:
                    cancelled = pc  # 取消须穿透，不当作「可跳过的轨失败」
                except Exception as te:  # noqa: BLE001 — 单轨失败用存活轨继续
                    logger.warning("[%s] 研究轨 %d 失败（跳过，用存活轨继续）: %s",
                                   state.pipeline_id, i + 1, te)
        if cancelled is not None:
            raise cancelled
        if not results:
            raise RuntimeError("所有并行研究轨均失败，研究阶段失败")

        survived = len(results)
        degraded = survived < n
        upd(96, f"合并 {survived}/{n} 条研究轨…")

        # 按轨序确定性合并
        ordered = [results[i] for i in sorted(results)]
        labeled_reports = [(title, res["report"]) for (title, _td, res) in ordered]
        labeled_dossiers = [(title, res.get("actor_dossier") or "") for (title, _td, res) in ordered]
        track_dirs = [td for (_t, td, _r) in ordered]

        merged_report = merge_track_reports(labeled_reports)
        merged_dossier = merge_track_reports(labeled_dossiers)
        merged_sources = merge_sources_union([res.get("sources") for (_t, _td, res) in ordered])
        merged_actors, _cast_audit = merge_actors_objs([res.get("actors") for (_t, _td, res) in ordered])
        merged_timeline = merge_list_dedup([res.get("timeline") for (_t, _td, res) in ordered])
        merged_quant = merge_list_dedup([_read_json(os.path.join(td, "quantitative.json")) for td in track_dirs])
        merged_contested = merge_list_dedup([_read_json(os.path.join(td, "contested.json")) for td in track_dirs])
        track_metas = [(_read_json(os.path.join(td, "meta.json")) or {}) for td in track_dirs]
        merged_rq = merge_research_quality(track_metas)
        merged_pm = pick_freshest_markets(
            [_read_json(os.path.join(td, "prediction_markets.json")) for td in track_dirs])

        if degraded:
            merged_rq["degraded"] = True
            merged_rq.setdefault("degradation", [])
            merged_rq["degradation"].append(f"parallel-tracks {survived}/{n} survived")

        # 合并 meta：以首个非空轨 meta 为基底（其它观测字段自洽），覆写 research_quality
        # 为「取最小值」的合并块，并按合并后的 sources 重算 source_tiers/计数（诚实）。
        base_meta: dict = next((m for m in track_metas if m), {})
        merged_meta = dict(base_meta)
        merged_meta["research_quality"] = merged_rq
        merged_meta["source_tiers"] = _source_tier_histogram(merged_sources)
        merged_meta["sources_count"] = len(merged_sources)
        if isinstance(merged_actors, dict):
            merged_meta["actors_count"] = len(merged_actors.get("actors") or [])
            merged_meta["relationships_count"] = len(merged_actors.get("relationships") or [])
        merged_meta["parallel_research"] = {
            "requested": n, "survived": survived,
            "angles": [angles[i][0] for i in sorted(results)],
        }

        # W9-7：合并卷宗的一次有界 LLM 整理——合并 K 份执行摘要为统一开篇、点名跨轨冲突的
        # 头条数字、'# Track N' H1 降级为 Part H2。只喂各轨执行摘要（cap ~30K 字符）；
        # 失败回退纯拼接（与今日逐字节一致）。存活 <2 轨时无需整理。
        if survived >= 2 and bool(getattr(Config, "RESEARCH_TRACK_RECONCILE", True)):
            upd(97, "整理多轨卷宗（合并执行摘要 + 跨轨分歧）…")
            merged_report, _rec_audit = reconcile_track_reports(merged_report, labeled_reports)
            state.options["research_track_reconcile"] = _rec_audit
            if not _rec_audit.get("applied"):
                logger.info("[%s] 卷宗整理未应用（%s），保留纯拼接",
                            state.pipeline_id, _rec_audit.get("reason"))

        # 落盘合并产物到 handoff_dir（下游/resume 读的正是这些文件）
        report_path = os.path.join(handoff_dir, "research_report.md")
        write_text_atomic(report_path, merged_report)
        if merged_dossier.strip():
            write_text_atomic(os.path.join(handoff_dir, "actor_dossier.md"), merged_dossier)
        if isinstance(merged_actors, dict):
            write_json_atomic(os.path.join(handoff_dir, "actors.json"), merged_actors)
        if merged_sources:
            write_json_atomic(os.path.join(handoff_dir, "sources.json"), merged_sources)
        if merged_timeline:
            write_json_atomic(os.path.join(handoff_dir, "timeline.json"), merged_timeline)
        if merged_quant:
            write_json_atomic(os.path.join(handoff_dir, "quantitative.json"), merged_quant)
        if merged_contested:
            write_json_atomic(os.path.join(handoff_dir, "contested.json"), merged_contested)
        if isinstance(merged_pm, dict):
            write_json_atomic(os.path.join(handoff_dir, "prediction_markets.json"), merged_pm)
        write_json_atomic(os.path.join(handoff_dir, "meta.json"), merged_meta)

        # 合并遥测：token/工具量求和；wall_s 取最大（并行）；model/depth 取首轨。
        tels = [res.get("research_telemetry") or {} for (_t, _td, res) in ordered]

        def _sum(key: str) -> int:
            return sum(int(t.get(key) or 0) for t in tels)

        merged_tel = {
            "model": (tels[0].get("model") if tels else None) or Config.DEERFLOW_MODEL,
            "depth": tels[0].get("depth") if tels else None,
            "tokens_in": _sum("tokens_in"),
            "tokens_out": _sum("tokens_out"),
            "tokens_total": _sum("tokens_total") or (_sum("tokens_in") + _sum("tokens_out")),
            "tool_calls": _sum("tool_calls"),
            "results": _sum("results"),
            "wall_s": max((float(t.get("wall_s") or 0.0) for t in tels), default=0.0),
            "parallel_tracks": survived,
        }

        state.options["parallel_research"] = merged_meta["parallel_research"]
        if degraded:
            logger.warning("[%s] 并行研究降级：%d/%d 轨存活", state.pipeline_id, survived, n)
        PipelineManager.save(state)

        return {
            "report": merged_report,
            "report_path": report_path,
            "actor_dossier": merged_dossier if merged_dossier.strip() else "",
            "actors": merged_actors,
            "sources": merged_sources,
            "timeline": merged_timeline,
            "exit_code": 0,
            "research_telemetry": merged_tel,
        }

    # -- 内部：嵌入预热 (R2-EXEC-7) ---------------------------------------

    def _maybe_warm_embedder(self, state: PipelineState, actors: Any) -> None:
        """R2-EXEC-7: best-effort warm the local embedder during research/ontology.

        Loads the SentenceTransformer model and pre-embeds actor names/aliases on a
        daemon thread so the graph stage meets a warm model + a populated disk cache
        (EMBED_DISK_CACHE_PATH) instead of paying first-encode latency mid-build.
        Default-OFF (EMBED_WARM_AT_RESEARCH); fully best-effort — failures are swallowed
        and the daemon thread can never block or wedge the pipeline."""
        if not getattr(Config, "EMBED_WARM_AT_RESEARCH", False):
            return
        try:
            from ..utils.actors import extract_actor_rows
            rows = extract_actor_rows(actors)
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            return
        texts: list[str] = []
        seen: set[str] = set()
        for r in rows:
            aliases = r.get("aliases") if isinstance(r.get("aliases"), list) else []
            for cand in [r.get("name"), *aliases]:
                s = str(cand or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    texts.append(s)
            if len(texts) >= 600:
                break
        if not texts:
            return
        pid = state.pipeline_id

        def _warm() -> None:
            try:
                from .graphiti_client.embedder import LocalSentenceTransformerEmbedder
                emb = LocalSentenceTransformerEmbedder()
                # 分批编码，写穿到共享磁盘缓存（按 model+text 寻址，graph 阶段命中即近零成本）。
                for i in range(0, len(texts), 128):
                    emb._encode(texts[i:i + 128])
                logger.info("[%s] 嵌入预热完成：%d 个 actor 名/别名", pid, len(texts))
            except Exception as _we:  # noqa: BLE001 — 预热纯增益，失败无声
                logger.debug("[%s] 嵌入预热跳过: %s", pid, _we)

        try:
            threading.Thread(target=_warm, name=f"embed-warm-{pid[:8]}", daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    # -- 内部：主流程 ------------------------------------------------------

    @classmethod
    def _run(cls, state: PipelineState) -> None:
        self = cls()
        task_manager = TaskManager()
        # EXECPLAN2 I-5-0/I-5-2: 把本次管线所有 LLM 调用归属到该 pipeline_id（contextvars）。
        from ..utils.telemetry import set_run_context, LLMMeter
        set_run_context(state.pipeline_id)
        # W9-3: attempt 起点初始化遥测增量落盘（捕获上一 attempt 的账作合并基底）。
        self._init_telemetry_flush(state)
        # I-8-1: 管线起飞即写首版 run.json（解析后的研究深度/模型/图谱/环境指纹），
        # 后续每阶段进入时把热切换出的报告/模拟 provider 钉入。
        self._write_run_manifest(state)
        # I-4-1: 钉入本进程的 owner 指纹 + 启动一个独立于阶段进度的壁钟心跳看护线程。
        # 心跳让 reconcile_orphans 把「死管线」与「慢但活（深研究/persona 静默数分钟）」区分开。
        hb_stop = self._start_heartbeat(state)
        try:
            # ---- Stage 0: RESEARCH ----
            upd = self._make_stage_updater(state, STAGE_RESEARCH)
            handoff_dir = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
            report_path = os.path.join(handoff_dir, "research_report.md")
            if os.path.exists(report_path) and len(_read_text(report_path).strip()) >= 400:
                upd(95, "复用已有研究报告，跳过 DeerFlow 研究阶段…")
                research = _load_research_handoff(handoff_dir)
                state.research_pid = None
                self._complete_stage(state, STAGE_RESEARCH, "研究报告已恢复")
            else:
                upd(1, "准备深度研究…")
                def _persist_research_pid(pid: int) -> None:
                    state.research_pid = pid
                    PipelineManager.save(state)

                # PAR-2：多角度并行研究轨。>1 时并行跑 K 个研究子进程（每轨角度特化）后
                # 确定性合并回 handoff/；=1 时走今日单轨路径（下方 else 逐字节不变）。
                try:
                    _n_tracks = max(1, int(getattr(Config, "RESEARCH_PARALLEL_TRACKS", 3) or 3))
                except (TypeError, ValueError):
                    _n_tracks = 3
                if _n_tracks > 1:
                    research = self._run_parallel_research_tracks(
                        state, handoff_dir, upd, _n_tracks)
                else:
                    # ITEM-3：本次研究阶段是 resume/orphan-reclaim 重试且 handoff_dir 已有
                    # checkpoint（上一轮崩溃/超时前写下）→ 续跑而非全量重启。首跑无 checkpoint → False。
                    _resume_research = _has_research_checkpoint(handoff_dir)
                    if _resume_research:
                        upd(1, "检测到研究断点，续跑（复用已完成 pass）…")
                    research = DeerFlowResearchRunner.run(
                        state.prompt,
                        handoff_dir,
                        on_progress=upd,
                        depth=state.options.get("depth"),
                        language=state.options.get("research_language"),  # T5.5
                        model=state.options.get("research_model"),        # T5.5
                        cancel_event=cls._cancel_events.get(state.pipeline_id),
                        on_spawn=_persist_research_pid,
                        resume=_resume_research,
                        # W9-9：图谱已存在（resume-with-graph/fork/continue 血统）时接入 KG MCP；
                        # 首跑 graph_id 为 None → env 与今日逐字节一致。
                        kg_graph_id=state.graph_id,
                    )
                state.research_pid = None  # 子进程已结束，清掉以免 reconcile 误杀复用 PID
                # PAR-2：并行轨 PID 清单也一并清空（研究阶段已结束，避免 reconcile 误杀复用 PID）。
                state.options.pop("research_track_pids", None)
                self._complete_stage(state, STAGE_RESEARCH, "研究完成")
            report_md: str = research["report"]
            # W9-11: 研究卷宗定稿后跑确定性编辑 lint（report_lint 由报告链工作流并行落地；
            # 模块缺失 → ImportError → 静默跳过）。清理 [citation:...] 残渣、pass/working-notes
            # 叙述泄漏等机器语法；清洗结果写回 handoff 的 research_report.md（下游/resume 同源）。
            if bool(getattr(Config, "REPORT_LINT", True)):
                try:
                    from .report_lint import lint_report
                    _lint_lang = (state.options.get("research_language")
                                  or getattr(Config, "DEERFLOW_RESEARCH_LANGUAGE", None))
                    if not _lint_lang:
                        # 无显式语言时按正文 CJK 占比推断（lint 用它决定改写文案的语言；
                        # report_lint._is_zh 会把空串当中文，英文卷宗须显式给 'English'）。
                        _sample = report_md[:20000]
                        _cjk_n = len(re.findall(r"[一-鿿]", _sample))
                        _lint_lang = "Chinese" if _cjk_n > max(1, len(_sample)) * 0.05 else "English"
                    _cleaned, _lint_rep = lint_report(report_md, _lint_lang, mode="research")
                    if isinstance(_cleaned, str) and _cleaned.strip() and _cleaned != report_md:
                        report_md = _cleaned
                        research["report"] = _cleaned
                        try:
                            from ..utils.atomic import write_text_atomic
                            write_text_atomic(
                                os.path.join(handoff_dir, "research_report.md"), _cleaned)
                        except Exception as _lw:  # noqa: BLE001 — 写回失败不阻断（内存版已清洗）
                            logger.warning("[%s] lint 后写回 research_report.md 失败: %s",
                                           state.pipeline_id, _lw)
                    if isinstance(_lint_rep, dict):
                        state.options["research_lint"] = {
                            k: v for k, v in _lint_rep.items()
                            if isinstance(v, (int, float, str, bool))
                        }
                except ImportError:
                    logger.debug("report_lint 未部署，跳过研究卷宗 lint")
                except Exception as _lint_err:  # noqa: BLE001 — lint 是增强，失败保留原文
                    logger.warning("[%s] 研究卷宗 lint 跳过: %s", state.pipeline_id, _lint_err)
            # 双轨 Track B：角色本体档案（actor_dossier.md）。它是本体/角色抽取的主种子，
            # 研究报告（Track A）作为补充上下文。旗标关闭或 Track B 缺失时为 None/""，
            # 下游 document_texts/chunks 退化为单轨，与今日逐字节一致。
            dossier_md = research.get("actor_dossier")
            actors = research.get("actors")
            # NEXTSTEPS P3-2: 抽取后跨轨去重，把同一实体的重复行合并为规范行（默认开；只会收紧
            # cast，无重复时 no-op）。在下游 ontology/graph/prepare/report 用 actors 之前完成。
            if getattr(Config, "CAST_RECONCILE", True) and actors:
                try:
                    from ..utils.actors import reconcile_cast as _reconcile
                    _recon, _audit = _reconcile(actors)
                    if _audit.get("merged"):
                        actors = _recon
                        research["actors"] = _recon  # 让下游 sources/report 也用规范 cast
                        _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                        try:
                            from ..utils.atomic import write_json_atomic
                            write_json_atomic(os.path.join(_hd, "actors.json"), _recon)
                            write_json_atomic(os.path.join(_hd, "cast_reconciliation.json"), _audit)
                            state.artifacts["cast_reconciliation"] = os.path.join(_hd, "cast_reconciliation.json")
                        except Exception:  # noqa: BLE001
                            pass
                        state.options["cast_reconciliation"] = {
                            "n_before": _audit.get("n_before"), "n_after": _audit.get("n_after"),
                            "merges": len(_audit.get("merged") or []),
                        }
                        logger.info("[%s] cast 去重：%s→%s（合并 %s 组）", state.pipeline_id,
                                    _audit.get("n_before"), _audit.get("n_after"),
                                    len(_audit.get("merged") or []))
                except Exception as _rc_err:  # noqa: BLE001 — 去重为增强，失败回退原 cast
                    logger.warning("[%s] cast 去重跳过: %s", state.pipeline_id, _rc_err)
            # I-5-7: 把研究阶段遥测并入统一计量（stash 到 options + 喂给 meter）。
            self._record_research_telemetry(state, research.get("research_telemetry"))
            # R2-EXEC-7: 趁本体/建图阶段尚未到来，后台预热嵌入器并预嵌 actor 名/别名
            # （默认关，EMBED_WARM_AT_RESEARCH；纯增益、daemon 线程、失败无声）。
            self._maybe_warm_embedder(state, actors)
            # I-0-3: 透传研究覆盖度/质量记分牌（meta.json → options，纯观测，永不硬失败）。
            self._surface_research_quality(state, handoff_dir)

            # NEXTSTEPS P3-4: 计算并透传 dossier 载荷字段覆盖率（纯观测；让"空壳种子"可见）。
            try:
                from ..utils.actors import dossier_coverage as _dcov
                cov = _dcov(actors)
                state.options["dossier_coverage"] = cov
                _weak = []
                if cov.get("n_actors", 0) and cov.get("pct_actors_with_incentives", 0) < 0.34:
                    _weak.append("多数 actor 缺激励结构")
                if cov.get("n_tier12", 0) and cov.get("pct_tier12_with_worldview", 0) < 0.34:
                    _weak.append("核心 actor 多缺世界观")
                if cov.get("n_relationships", 0) and cov.get("pct_edges_valenced", 0) < 0.2:
                    _weak.append("关系网几乎无显式 valence")
                if cov.get("n_relationships", 0) == 0:
                    _weak.append("无关系边")
                if _weak:
                    state.options["dossier_coverage_warning"] = "；".join(_weak)
                    logger.warning("[%s] dossier 覆盖偏低：%s", state.pipeline_id, "；".join(_weak))
            except Exception as _cov_err:  # noqa: BLE001 — 覆盖度纯观测，失败不影响主流程
                logger.debug("[%s] dossier_coverage 计算跳过: %s", state.pipeline_id, _cov_err)

            # R2-RES-3: 由 dossier 覆盖度 + 来源层级 + 研究质量记分牌派生一个咨询性
            # forecast_confidence_penalty 写入 options，供发布门后续消费（gate refine 推迟；
            # 此处纯写值，不读不阻断，永不 wedge）。
            self._surface_forecast_confidence_penalty(state, handoff_dir)

            if state.mode == "research_only":
                state.status = "completed"
                state.global_progress = 100
                PipelineManager.save(state)
                if state.task_id:
                    task_manager.complete_task(state.task_id, result={
                        "pipeline_id": state.pipeline_id,
                        "mode": state.mode,
                        "report_path": research.get("report_path"),
                    })
                logger.info(f"[{state.pipeline_id}] research_only 完成")
                return

            # ---- Stage 1: ONTOLOGY (用研究报告做种子) ----
            upd = self._make_stage_updater(state, STAGE_ONTOLOGY)
            self._update_manifest(state, STAGE_ONTOLOGY)  # I-8-1: 钉入本阶段实际 provider
            project_name = state.options.get("project_name") or f"研究预测 {state.pipeline_id}"
            project = ProjectManager.get_project(state.project_id) if state.project_id else None
            if project is not None and project.ontology:
                upd(100, "复用已有本体…")
                self._complete_stage(state, STAGE_ONTOLOGY, "本体已恢复")
            else:
                if project is None:
                    upd(10, "用研究报告创建项目…")
                    project = ProjectManager.create_project(name=project_name)
                    project.simulation_requirement = state.prompt
                    ProjectManager.save_extracted_text(project.project_id, report_md)
                    project.total_text_length = len(report_md)
                    file_entry: dict[str, Any] = {"filename": "research_report.md", "size": len(report_md.encode("utf-8"))}
                    project.files.append(file_entry)
                    ProjectManager.save_project(project)
                    state.project_id = project.project_id
                    PipelineManager.save(state)

                upd(40, "生成本体（LLM）…")
                generator = OntologyGenerator()
                # I-1-3 领域自适应本体：把研究的 central_question + actors 直方图喂给生成器，
                # 让实体/关系类型贴合预测领域，而不是永远走通用 social_opinion 模板。
                # 模板由 Config.ONTOLOGY_TEMPLATE 控制（general_forecast 有内置兜底回退到
                # social_opinion，故传入即安全）；缺字段时静默降级到旧行为。
                # NEXTSTEPS P3-3: ONTOLOGY_FROM_DOSSIER 开启时，把已实现 actor 阵容投影成本体种子
                # 约束拼到 additional_context 末尾（单一真源，避免从散文重新派生导致 schema/instance
                # 漂移）。默认关 → 行为与今日一致。
                _addl_ctx = _actors_to_context(actors)
                if getattr(Config, "ONTOLOGY_FROM_DOSSIER", False):
                    try:
                        from ..utils.actors import ontology_seed_block as _onto_seed
                        _seed_blk = _onto_seed(actors)
                        if _seed_blk:
                            _addl_ctx = ((_addl_ctx + "\n\n") if _addl_ctx else "") + _seed_blk
                    except Exception:  # noqa: BLE001 — 本体种子为可选增强，失败回退
                        pass
                ontology = generator.generate(
                    # 双轨喂料：角色档案在前作为本体/角色种子，研究报告在后作补充。
                    # dossier_md 为空（旗标关闭/Track B 失败）时退化为 [report_md]，与今日逐字节一致。
                    document_texts=([dossier_md] if dossier_md and dossier_md.strip() else []) + [report_md],
                    simulation_requirement=state.prompt,
                    additional_context=_addl_ctx,
                    # ONT-2: 传 None 才让 ONTOLOGY_AUTO_SELECT（默认开）真正生效——此前恒传
                    # Config.ONTOLOGY_TEMPLATE（非 None），自动选模板在主管线路径是死代码，
                    # 未手工改 ONTOLOGY_TEMPLATE 的部署会把市场/地缘预测硬塞进社媒 schema。
                    # 关闭 AUTO_SELECT 时 _resolve_template(None) 仍回落 Config.ONTOLOGY_TEMPLATE，
                    # 显式配置照旧生效。（关键词误路由已由 ONT-11 的双词组合收紧。）
                    template=None,
                    central_question=(actors.get("central_question") if isinstance(actors, dict) else None),
                    actors=actors,
                )
                # CLAUDE §12.1 / CODEX Step 1：保留完整本体对象。set_ontology 与现有读者只读
                # entity_types/edge_types（见 graph_builder.set_ontology），故除这两键外，生成器
                # 还可能返回的 archetype 分类的类型、边族/valence、analysis_summary、schema_version、
                # domain 等键在被消费前都是惰性的——保留它们零风险，且为下游 valenced/archetype
                # 消费打底。旗标关闭、或生成器只返回两键时，行为与旧逻辑逐字节一致。
                if getattr(Config, "ONTOLOGY_RICH_SCHEMA", True) and isinstance(ontology, dict):
                    project.ontology = dict(ontology)
                    project.ontology["entity_types"] = ontology.get("entity_types", [])
                    project.ontology["edge_types"] = ontology.get("edge_types", [])
                else:
                    project.ontology = {
                        "entity_types": ontology.get("entity_types", []),
                        "edge_types": ontology.get("edge_types", []),
                    }
                project.analysis_summary = ontology.get("analysis_summary", "")
                project.status = ProjectStatus.ONTOLOGY_GENERATED
                ProjectManager.save_project(project)
                # T6.3: 把本体落到 handoff/ontology.json，供 artifact 深链。
                # ONT-10: 原子写（对齐 actors.json 的 write_json_atomic 约定）——半写的
                # ontology.json 会让 resume 校验静默强制重建；失败留 warning 而非无声吞掉。
                try:
                    from ..utils.atomic import write_json_atomic
                    _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                    write_json_atomic(os.path.join(_hd, "ontology.json"), project.ontology)
                except Exception as _oe:  # noqa: BLE001 — 落盘失败非致命，但必须可见
                    logger.warning("[%s] ontology.json 落盘失败: %s", state.pipeline_id, _oe)
                self._complete_stage(state, STAGE_ONTOLOGY, "本体生成完成")

            # ---- Stage 2: GRAPH ----
            upd = self._make_stage_updater(state, STAGE_GRAPH)
            self._update_manifest(state, STAGE_GRAPH)  # I-8-1
            graph_stage_done = state.stages.get(STAGE_GRAPH) and state.stages[STAGE_GRAPH].status == "completed"
            graph_id = state.graph_id or getattr(project, "graph_id", None)
            _reuse_graph = bool(graph_stage_done and graph_id)
            # I-4-3: 复用前先按产物清单校验 GRAPH 阶段的文件产物（communities.json 等）未被半写/篡改；
            # 不符则回落重建并留痕（与下方既有的实体数健康检查并列，两道防线各管一半）。
            if _reuse_graph and not self._reuse_ok(state, STAGE_GRAPH):
                _reuse_graph = False
                state.options["resumed_stage_validation"] = "graph_rebuilt_manifest_mismatch"
                logger.info("[%s] 图谱产物清单校验未通过，回落到重建", state.pipeline_id)
            if _reuse_graph:
                # T6.1/T2.7: 复用前做廉价健康检查——0 实体的图谱被复用会在 PREPARE 阶段崩溃，
                # 故实体数为 0（或查询失败）时回落到重建，并留痕。
                try:
                    from .zep_entity_reader import ZepEntityReader
                    _cnt = len(ZepEntityReader().filter_defined_entities(graph_id, enrich_with_edges=False).entities)
                    state.options["graph_entity_count"] = _cnt
                    if _cnt == 0:
                        _reuse_graph = False
                        state.options["resumed_stage_validation"] = "graph_rebuilt_0_entities"
                        logger.info("[%s] 复用图谱实体数为 0，回落到重建", state.pipeline_id)
                except Exception as e:
                    _reuse_graph = False
                    state.options["resumed_stage_validation"] = "graph_rebuilt_healthcheck_error"
                    logger.warning("[%s] 图谱健康检查失败，回落到重建: %s", state.pipeline_id, e)
            if _reuse_graph:
                upd(100, "复用已有知识图谱…")
                state.graph_id = graph_id
                # KG-6: 本体注册是进程内存字典——后端重启后的复用路径若不重注册，每次
                # SIM_GRAPH_FEEDBACK 写回都以 entity_types=None 抽取、落成裸 'Entity' 标签，
                # 随后被 typed-entity 过滤器整体丢弃。幂等、纯内存写，绝不失败该阶段。
                try:
                    if project is not None and project.ontology:
                        GraphBuilderService(api_key=Config.ZEP_API_KEY).set_ontology(graph_id, project.ontology)
                except Exception as _ro_err:  # noqa: BLE001
                    logger.warning("reuse-path set_ontology skipped: %s", _ro_err)
                self._complete_stage(state, STAGE_GRAPH, "图谱已恢复")
            else:
                upd(5, "构建知识图谱…")
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                # ORCH-1(5): 切块前剔除 base64/data-URI 内嵌图片（保留 alt/图注）——
                # 图表型报告的单张内嵌图就是数万字符的 base64 噪声，切块后会整段
                # 灌进图谱 episode。仅影响建图输入；本体/报告落盘仍用原文。
                _graph_report_md = _strip_data_uri_images(report_md)
                _graph_dossier_md = _strip_data_uri_images(dossier_md) if dossier_md else dossier_md
                chunks = TextProcessor.split_text(_graph_report_md, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
                # 双轨：角色本体档案非空时也切块并前置注入（角色中心内容先种入图谱，
                # 再由广覆盖研究报告补充）。dynamic-band 用 len(chunks) 重算仍成立。旗标关闭/
                # Track B 缺失时 dossier_md 为 None/""，chunks 与今日逐字节一致。
                #
                # RESEARCH-2 / ONTO-7: 建图输入源可配置，默认 'both' 与今日逐字节一致。
                #   'dossier_only' (RESEARCH-2): dossier 非空时只用 dossier 建图；研究报告仅
                #       继续喂本体 + 报告上下文，不再切块入图。
                #   'report_only'  (ONTO-7): 跳过 dossier 重切块——GRAPH_SEED_FROM_ACTORS 已把
                #       cast 作为 typed 边种入，dossier 散文在此冗余。
                # 未知值或 dossier 缺失/为空一律回退 'both'/当前行为（degrade-safe）。
                _chunk_src = str(getattr(Config, "GRAPH_CHUNK_SOURCE", "both") or "both").strip().lower()
                _have_dossier = bool(dossier_md and dossier_md.strip())
                if _have_dossier and _chunk_src == "dossier_only":
                    chunks = TextProcessor.split_text(_graph_dossier_md, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
                    state.options["graph_chunk_source"] = "dossier_only"
                elif _chunk_src == "report_only":
                    # 报告 chunk 已在 `chunks` 中；dossier 有意不再切块。
                    state.options["graph_chunk_source"] = "report_only"
                elif _have_dossier:
                    chunks = TextProcessor.split_text(_graph_dossier_md, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP) + chunks
                    state.options["graph_chunk_source"] = "both"
                self._recompute_dynamic_bands(state, chunk_count=len(chunks))  # T6.7: 已知 chunk 数→重排区间
                graph_id = builder.create_graph(name=project.name)
                # EXECPLAN2 F-3-1（跨文件兜底）：set_ontology 已对无名条目逐项跳过；这里再包一层
                # try/except，确保即便整段本体异常也不至于中断 GRAPH 阶段（无本体则按纯文本抽取建图）。
                try:
                    builder.set_ontology(graph_id, project.ontology)
                except Exception as _e:
                    logger.warning(f"set_ontology 失败，回退为无本体建图: {_e}")

                # T2.2/T2.3: 在文本抽取前，把研究确认的 actors + relationships 作为 typed 边
                # 种入图谱；valid_at 锚定研究 as_of（不是建图时刻），后续文本抽取按名+embedding
                # dedup「富化」这些种子节点而非重复创建。as_of 同时作为所有研究 chunk 的
                # reference_time，给 panorama_search 的 active/historical 切分一个真实双时态轴。
                # R2-RES-7: validate the research as_of_date before using it as the
                # bi-temporal anchor; fall back to newest source date / run date on a
                # future, pre-evidence, or unparseable value. Gated default-on; any
                # error or a disabled flag reverts to the plain parse (today's behavior).
                if getattr(Config, "VALIDATE_AS_OF_DATE", True):
                    try:
                        as_of, _as_of_note = self._validate_as_of_date(actors, research.get("sources"))
                        if _as_of_note:
                            state.options["as_of_date_correction"] = _as_of_note
                            logger.warning("[%s] %s → %s", state.pipeline_id, _as_of_note,
                                           as_of.date() if as_of else None)
                    except Exception as _ae:  # noqa: BLE001 — 校验失败回退原始解析
                        logger.debug("[%s] as_of 校验跳过: %s", state.pipeline_id, _ae)
                        as_of = parse_as_of((actors or {}).get("as_of_date")) if isinstance(actors, dict) else None
                else:
                    as_of = parse_as_of((actors or {}).get("as_of_date")) if isinstance(actors, dict) else None
                if Config.GRAPH_SEED_FROM_ACTORS and actors:
                    try:
                        seeded = builder.seed_actors(graph_id, actors, valid_at=as_of)
                        upd(8, f"已注入 {seeded} 条调研关系/角色种子…")
                        state.options["graph_seeded_edges"] = seeded
                    except Exception as e:
                        logger.warning("[%s] actor seeding skipped: %s", state.pipeline_id, e)

                def add_cb(msg: str, ratio: float):
                    upd(int(10 + ratio * 55), msg)

                # batch_size 10：Zep graph.add 按 episode 异步处理，批量提交吞吐近似线性；
                # 3 是早期保守值，研究报告动辄上百 chunk 时建图要多等数分钟。
                uuids = builder.add_text_batches(
                    graph_id, chunks, batch_size=10, progress_callback=add_cb, reference_time=as_of
                )

                # KG-5: 量化 ingest 丢失。builder 对失败 chunk 是跳过续跑（正确的韧性），但跳块
                # 比例超过 GRAPH_MAX_SKIPPED_RATIO 时图谱是「安静残缺」的——留痕到 options，
                # 由 _enforce_pipeline_health 折入 pipeline_health 的 graph 阶段降级。
                try:
                    _ing = getattr(builder, "last_ingest_stats", None)
                    if isinstance(_ing, dict) and int(_ing.get("total") or 0) > 0:
                        _ing_total = int(_ing.get("total") or 0)
                        _ing_failed = int(_ing.get("failed") or 0)
                        state.options["graph_skipped_chunks"] = _ing_failed
                        state.options["graph_total_chunks"] = _ing_total
                        _skip_ratio = _ing_failed / float(_ing_total)
                        _max_skip = float(getattr(Config, "GRAPH_MAX_SKIPPED_RATIO", 0.3) or 0.3)
                        if _skip_ratio > _max_skip:
                            state.options["graph_ingest_degraded_ratio"] = round(_skip_ratio, 3)
                            logger.warning(
                                "[%s] 建图跳块比例 %.0f%%（%d/%d）超过阈值 %.0f%% —— 图谱残缺，"
                                "GRAPH 阶段将标记 degraded", state.pipeline_id,
                                _skip_ratio * 100, _ing_failed, _ing_total, _max_skip * 100)
                except Exception:  # noqa: BLE001 — ingest 统计纯观测
                    pass

                def wait_cb(msg: str, ratio: float):
                    upd(int(65 + ratio * 33), msg)

                builder._wait_for_episodes(uuids, wait_cb)

                # T2.4: best-effort 社区发现（派系/联盟），失败不影响建图。持久化到 handoff/communities.json
                # 供 sim-config 回声室种子（T3.4）与报告派系图（T4.2）复用。
                if Config.GRAPH_BUILD_COMMUNITIES:
                    try:
                        upd(99, "检测派系/社区结构…")
                        communities = builder.build_communities(graph_id)
                        if communities:
                            handoff_dir = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                            os.makedirs(handoff_dir, exist_ok=True)
                            with open(os.path.join(handoff_dir, "communities.json"), "w", encoding="utf-8") as cf:
                                json.dump(communities, cf, ensure_ascii=False, indent=2)
                        state.options["graph_communities"] = len(communities)
                        logger.info("[%s] 社区发现: %d 个", state.pipeline_id, len(communities))
                    except Exception as e:
                        logger.warning("[%s] community detection skipped: %s", state.pipeline_id, e)

                # I-1-4: best-effort entity resolution / canonical-alias merge (no LLM).
                # Runs AFTER communities so merges land before retrieval reads the graph;
                # KG-10b: default-ON (GRAPH_RESOLVE_ENTITIES, config.py 默认 true——并行建图
                # 的 dedup 安全网)。Never blocks the build; audited to handoff/entity_merges.json.
                if Config.GRAPH_RESOLVE_ENTITIES:
                    try:
                        upd(99, "实体消解/规范别名合并…")
                        from .zep_entity_resolver import resolve_entities
                        audit = resolve_entities(graph_id, actors)
                        if audit.get("merges"):
                            handoff_dir = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                            os.makedirs(handoff_dir, exist_ok=True)
                            with open(os.path.join(handoff_dir, "entity_merges.json"), "w", encoding="utf-8") as mf:
                                json.dump(audit, mf, ensure_ascii=False, indent=2)
                        state.options["entity_merges"] = audit.get("merged_nodes", 0)
                        logger.info("[%s] 实体消解: 扫描 %d 合并 %d 节点",
                                    state.pipeline_id, audit.get("nodes_scanned", 0),
                                    audit.get("merged_nodes", 0))
                    except Exception as e:
                        logger.warning("[%s] entity resolution skipped: %s", state.pipeline_id, e)

                # W9-10: 图谱裁剪——把 KG 收敛到研究确认的关键 actor 周边（用户明确要求）。
                # 在实体消解之后、中心度先验计算之前执行，使 graph_priors 反映裁剪后的图。
                # graph_pruner 由 kg-backend 工作流并行落地（模块缺失 → ImportError → 静默跳过）。
                if bool(getattr(Config, "GRAPH_PRUNE_ENABLED", False)):
                    try:
                        from .graph_pruner import prune_graph
                        upd(99, "裁剪外围图谱节点（收敛到关键 actor）…")
                        _actor_rows = (actors.get("actors") if isinstance(actors, dict) else None) or []
                        _prune_audit = prune_graph(graph_id=graph_id, actors=_actor_rows)
                        if isinstance(_prune_audit, dict) and _prune_audit:
                            state.options["graph_prune"] = {
                                k: v for k, v in _prune_audit.items()
                                if isinstance(v, (int, float, str, bool))
                            }
                            try:
                                from ..utils.atomic import write_json_atomic
                                _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                                write_json_atomic(os.path.join(_hd, "graph_prune.json"), _prune_audit)
                                state.artifacts["graph_prune"] = os.path.join(_hd, "graph_prune.json")
                            except Exception:  # noqa: BLE001 — 审计落盘失败不影响裁剪本身
                                pass
                            logger.info("[%s] 图谱裁剪完成: %s", state.pipeline_id,
                                        state.options.get("graph_prune"))
                    except ImportError:
                        logger.debug("graph_pruner 未部署，跳过图谱裁剪")
                    except Exception as _pr_err:  # noqa: BLE001 — 裁剪失败绝不拖垮建图
                        logger.warning("[%s] 图谱裁剪跳过: %s", state.pipeline_id, _pr_err)

                # J1: 并发抽取（GRAPH_BUILD_CONCURRENCY>1）的重名实体守卫。
                # add_episodes_concurrent 的 read-before-commit dedup 在并发下可能让两个 episode
                # 都漏掉对方在飞的同名新节点，各自建出重名重复节点（runtime 文档 F-2-5；DB 无名唯一约束）。
                # 实体消解（GRAPH_RESOLVE_ENTITIES，默认关）是缓解手段，但与并发解耦。这里在并发>1 时
                # 做一遍重名 DETECTION：按 NFKC 规范名分组、统计含 >1 节点的组数，记入 graph_integrity 指标，
                # 并在未开消解却检出重复时打 WARNING。全程 best-effort，失败不影响建图；并发==1 时不触发，
                # 默认路径逐字节不变。
                _effective_concurrency = int(getattr(Config, "GRAPH_BUILD_CONCURRENCY", 1) or 1)
                if _effective_concurrency > 1:
                    try:
                        from ..utils.actors import normalize_name as _normalize_name
                        from .graphiti_client.runtime import get_runtime
                        _nodes = get_runtime().all_entity_nodes(graph_id) or []
                        _groups: dict[str, int] = {}
                        for _nd in _nodes:
                            _norm = _normalize_name(_nd.get("name", ""))
                            if not _norm:
                                continue
                            _groups[_norm] = _groups.get(_norm, 0) + 1
                        _dup_groups = sum(1 for _c in _groups.values() if _c > 1)
                        state.options["graph_duplicate_name_groups"] = _dup_groups
                        if _dup_groups and not Config.GRAPH_RESOLVE_ENTITIES:
                            logger.warning(
                                "[%s] 并发建图(GRAPH_BUILD_CONCURRENCY=%d)检出 %d 组重名实体节点，"
                                "而实体消解(GRAPH_RESOLVE_ENTITIES)未开启——重复节点会分裂检索召回/低估中心性。"
                                "建议设 GRAPH_RESOLVE_ENTITIES=true 合并，或回退 GRAPH_BUILD_CONCURRENCY=1",
                                state.pipeline_id, _effective_concurrency, _dup_groups,
                            )
                        elif _dup_groups:
                            logger.info("[%s] 并发建图检出 %d 组重名实体（已开启实体消解）",
                                        state.pipeline_id, _dup_groups)
                    except Exception as e:
                        logger.warning("[%s] duplicate-name detection skipped: %s", state.pipeline_id, e)

                # KG cookbook 第4步：建图后跑结构完整性检查（节点/边/弱连通分量/枢纽），
                # 把分量数等指标记入 state.options 并 emit 日志/欠合并告警（实现在 _get_graph_info）。
                try:
                    gi = builder._get_graph_info(graph_id)
                    state.options["graph_node_count"] = gi.node_count
                    state.options["graph_edge_count"] = gi.edge_count
                    state.options["graph_components"] = gi.components
                    # NEXTSTEPS P3-7: 持久化中心度先验（node_name→[0,1]）供 PREPARE 的 salience
                    # 排序融合 + 报告/UI 引用；此前 _get_graph_info 算完即丢弃。
                    # KG-7: 按 actors.json 策展别名组折叠（组内 MAX、ADD-only、旗标内部门控）——
                    # 同一现实 actor 的表面形不再把影响力信号打碎成小分片；无 actors/关旗标时原样返回。
                    if getattr(gi, "centrality", None):
                        try:
                            from ..utils.atomic import write_json_atomic
                            from .graph_builder import fold_priors_with_aliases
                            _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                            _folded = fold_priors_with_aliases(gi.centrality, actors)
                            write_json_atomic(os.path.join(_hd, "graph_priors.json"), _folded)
                            state.artifacts["graph_priors"] = os.path.join(_hd, "graph_priors.json")
                            state.options["graph_centrality_nodes"] = len(_folded)
                        except Exception:  # noqa: BLE001
                            pass
                    # KG-3: 结构咽喉先验（介数中心度 + 关节点，GRAPH_CHOKEPOINT_PRIORS 开启且
                    # 非空时才有值）。落独立文件，扁平的 graph_priors.json（name→centrality）
                    # 保持不变以兼容旧读者。
                    if getattr(gi, "betweenness", None) or getattr(gi, "chokepoints", None):
                        try:
                            from ..utils.atomic import write_json_atomic
                            _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                            _sp = os.path.join(_hd, "graph_priors_structural.json")
                            write_json_atomic(_sp, {
                                "betweenness": gi.betweenness or {},
                                "chokepoints": gi.chokepoints or [],
                            })
                            state.artifacts["graph_priors_structural"] = _sp
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as e:
                    logger.warning("[%s] graph integrity check skipped: %s", state.pipeline_id, e)

                project.graph_id = graph_id
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                state.graph_id = graph_id
                self._complete_stage(state, STAGE_GRAPH, "图谱构建完成")

            # ---- Stage 3: PREPARE ----
            upd = self._make_stage_updater(state, STAGE_PREPARE)
            sim_manager = SimulationManager()
            prepare_stage_done = state.stages.get(STAGE_PREPARE) and state.stages[STAGE_PREPARE].status == "completed"
            sim_state = sim_manager.get_simulation(state.simulation_id) if state.simulation_id else None
            # I-4-3: 复用前校验 PREPARE 产物（simulation_config.json / personas）未被半写/篡改；
            # 不符则当作未完成、走重建分支并留痕（半写的 sim_config 会让模拟/报告静默降级）。
            _prepare_reuse = bool(prepare_stage_done and sim_state is not None)
            if _prepare_reuse and not self._reuse_ok(state, STAGE_PREPARE):
                _prepare_reuse = False
                state.options["resumed_stage_validation"] = "prepare_rebuilt_manifest_mismatch"
                logger.info("[%s] 模拟环境产物清单校验未通过，回落到重建", state.pipeline_id)
            if _prepare_reuse:
                upd(100, "复用已有模拟环境…")
                self._complete_stage(state, STAGE_PREPARE, "环境已恢复")
            else:
                if prepare_stage_done and sim_state is None:
                    # 阶段标完成但模拟状态丢了（手动清理/磁盘损坏）：自愈重建，但要留痕。
                    logger.warning(
                        f"[{state.pipeline_id}] prepare 阶段已完成但模拟 "
                        f"{state.simulation_id} 不存在，重新创建模拟环境"
                    )
                upd(5, "创建模拟…")
                sim_state = sim_manager.create_simulation(project.project_id, graph_id, enable_twitter=True, enable_reddit=True)
                state.simulation_id = sim_state.simulation_id
                # XRUN-15: 新模拟 attempt 取代旧 attempt，清掉指向旧 sim/report 目录的
                # *_partial 临时产物指针（扫描器会为新 attempt 重新登记）。
                for _pk in [k for k in list(state.artifacts) if k.endswith("_partial")]:
                    state.artifacts.pop(_pk, None)
                PipelineManager.save(state)

                def prepare_cb(stage: str, progress: int, message: str, **_kwargs):
                    upd(max(5, min(99, int(progress))), f"{stage}: {message}")

                # persona 生成并发：CLI 提供方受本机 CLI 吞吐限制保持 3；
                # OpenAI 兼容 HTTP 提供方可以放心放大（每个 persona 1 次 LLM + 2 次 Zep 检索）。
                _is_http_provider = bool(Config.PROVIDER_META.get(Config.LLM_PROVIDER, {}).get('openai_compat'))
                # P3-7: 读取 GRAPH 阶段持久化的中心度先验，融入 agent-cap 的 salience 排序
                # （从 handoff 读，resume 也能拿到）。缺失则为 None → 排序行为不变。
                _graph_priors = None
                try:
                    _gp_path = os.path.join(
                        state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id),
                        "graph_priors.json")
                    if os.path.exists(_gp_path):
                        with open(_gp_path, "r", encoding="utf-8") as _gf:
                            _graph_priors = json.load(_gf)
                except Exception:  # noqa: BLE001
                    _graph_priors = None
                # SIM-11 (pairs with SIM-7): raise HTTP persona fan-out default 8→16
                # (configurable via PARALLEL_PROFILE_COUNT); CLI providers stay at 3.
                _pp = int(getattr(Config, "PARALLEL_PROFILE_COUNT", 16) or 16)
                # PREP-1: 把本次运行的真实轮数预算透传到事件排期（否则生成器只能按
                # OASIS_DEFAULT_MAX_ROUNDS 钳制，options.max_rounds < 默认时欠覆盖）。
                _prep_mr = state.options.get("max_rounds") or (Config.OASIS_DEFAULT_MAX_ROUNDS or None)
                sim_manager.prepare_simulation(
                    simulation_id=sim_state.simulation_id,
                    simulation_requirement=state.prompt,
                    document_text=report_md,
                    progress_callback=prepare_cb,
                    parallel_profile_count=_pp if _is_http_provider else 3,
                    actors=actors,  # 研究档案直通模拟准备：persona/配置以实证立场为准
                    graph_priors=_graph_priors,  # P3-7: 中心度先验（融入 salience 排序）
                    max_rounds=int(_prep_mr) if _prep_mr else None,
                    research_language=state.options.get("research_language"),  # PREP-4
                )
                self._complete_stage(state, STAGE_PREPARE, "环境就绪")

            # T4.6: 情景 overlay — 把影响力/立场覆盖 + 注入事件确定性落到 simulation_config.json
            _overlay = state.options.get("scenario_overlay")
            _run_already_done = bool(state.stages.get(STAGE_RUN) and state.stages[STAGE_RUN].status == "completed")
            if _overlay and not _run_already_done:
                try:
                    _cfg_path = os.path.join(
                        Config.OASIS_SIMULATION_DATA_DIR, sim_state.simulation_id, "simulation_config.json"
                    )
                    if os.path.exists(_cfg_path):
                        with open(_cfg_path, "r", encoding="utf-8") as _cf:
                            _cfg = json.load(_cf)
                        self.apply_scenario_overlay_to_config(_cfg, _overlay)
                        _tmp = _cfg_path + ".tmp"
                        with open(_tmp, "w", encoding="utf-8") as _cf:
                            json.dump(_cfg, _cf, ensure_ascii=False, indent=2)
                        os.replace(_tmp, _cfg_path)
                        logger.info("[%s] 情景 overlay 已应用到 simulation_config", state.pipeline_id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] 情景 overlay 应用跳过: %s", state.pipeline_id, e)

            # NEXTSTEPS P1-1/P1-2: 决策通道开启时，把 WorldState 种子（情景+基率）+ 时点跨度
            # （as_of→horizon，供子进程逐轮日期映射）注入 simulation_config.json，供 post-sim
            # 演化"结果世界态"。默认关 → 不注入，行为与今日逐字节一致。
            if getattr(Config, "SIM_DECISION_CHANNEL", False) and not _run_already_done:
                try:
                    from ..utils.actors import world_state_seed_from_actors as _ws_seed
                    _seed = _ws_seed(actors)
                    if _seed.get("scenarios"):
                        _seed["as_of_date"] = (actors.get("as_of_date") if isinstance(actors, dict) else None)
                        _seed["horizon_date"] = self._infer_horizon_date(state.prompt, actors)
                        _wcfg_path = os.path.join(
                            Config.OASIS_SIMULATION_DATA_DIR, sim_state.simulation_id, "simulation_config.json")
                        if os.path.exists(_wcfg_path):
                            from ..utils.atomic import write_json_atomic
                            with open(_wcfg_path, "r", encoding="utf-8") as _wf:
                                _wcfg = json.load(_wf)
                            _wcfg["world_state_seed"] = _seed
                            write_json_atomic(_wcfg_path, _wcfg)
                            logger.info("[%s] WorldState 种子已注入 simulation_config（%d 情景, horizon=%s）",
                                        state.pipeline_id, len(_seed["scenarios"]), _seed.get("horizon_date"))
                except Exception as _ws_err:  # noqa: BLE001
                    logger.warning("[%s] WorldState 种子注入跳过: %s", state.pipeline_id, _ws_err)

            # ---- Stage 4: RUN ----
            upd = self._make_stage_updater(state, STAGE_RUN)
            run_stage_done = state.stages.get(STAGE_RUN) and state.stages[STAGE_RUN].status == "completed"
            # I-4-3: 复用前校验 RUN 产物（run_summary.json）未被半写/篡改；不符则重跑模拟并留痕。
            if run_stage_done and not self._reuse_ok(state, STAGE_RUN):
                run_stage_done = False
                state.options["resumed_stage_validation"] = "run_rebuilt_manifest_mismatch"
                logger.info("[%s] 模拟结果产物清单校验未通过，回落到重跑", state.pipeline_id)
            if run_stage_done:
                upd(100, "复用已有模拟结果…")
                self._complete_stage(state, STAGE_RUN, "模拟已恢复")
            else:
                upd(2, "启动 OASIS 模拟…")
                run_kwargs: dict[str, Any] = {"platform": "parallel"}
                # T3.7: 每次运行的 max_rounds 优先，否则用 Config.OASIS_DEFAULT_MAX_ROUNDS（0→None=跑满）。
                _mr = state.options.get("max_rounds") or (Config.OASIS_DEFAULT_MAX_ROUNDS or None)
                if _mr:
                    run_kwargs["max_rounds"] = int(_mr)
                # T3.10: 打开「模拟 → 图谱」反馈回路（本地默认开），让报告阶段挖到的是模拟后的图谱。
                if Config.SIM_GRAPH_FEEDBACK and graph_id:
                    run_kwargs["enable_graph_memory_update"] = True
                    run_kwargs["graph_id"] = graph_id
                SimulationRunner.start_simulation(simulation_id=sim_state.simulation_id, **run_kwargs)
                # 轮询直到完成
                cancel_ev = cls._cancel_events.get(state.pipeline_id)
                _last_round_seen = (-1, -1)
                # B12: 停滞看门狗——记录上次「轮次推进」的时刻；长时间毫无进展视为卡死。
                _last_progress_at = time.monotonic()
                try:
                    _stall_s = float(getattr(Config, "PIPELINE_RUN_STALL_S", 1800) or 1800)
                except (TypeError, ValueError):
                    _stall_s = 1800.0
                # C1/C2: independent disk-based watchdog that force-stops a wedged sim even if
                # this poll loop blocks on the sim's IPC (the inline check below can't fire then).
                _wd_ctl = self._spawn_run_stall_watchdog(state.pipeline_id, sim_state.simulation_id, _stall_s)
                # ORCH-6: 看门狗退休放 finally——此前只在 COMPLETED 快乐路径置位 stop，取消/
                # FAILED/STOPPED/轮询异常路径会让 daemon 线程继续每 30s 轮询 run_state.json，
                # 并在 stall_s 后对已结束的模拟再次 stop_simulation、伴随整个报告阶段游荡。
                try:
                    while True:
                        if cancel_ev is not None and cancel_ev.is_set():
                            # 先停掉 OASIS 子进程再退出，避免取消后模拟继续烧额度
                            try:
                                SimulationRunner.stop_simulation(sim_state.simulation_id)
                            except Exception as stop_err:  # noqa: BLE001
                                logger.warning(f"[{state.pipeline_id}] 取消时停止模拟失败: {stop_err}")
                            raise PipelineCancelled("模拟已被用户取消")
                        rs = SimulationRunner.get_run_state(sim_state.simulation_id)
                        if rs is None:
                            raise RuntimeError("模拟运行状态丢失")
                        total = getattr(rs, "total_rounds", 0) or 0
                        cur = getattr(rs, "current_round", 0) or 0
                        # 仅在轮次推进时落盘进度，省掉每 5s 一次的无效 JSON 重写 + 任务更新
                        # （取消请求由循环顶部的检查兜底，最多延迟一个 5s 周期）
                        if (cur, total) != _last_round_seen:
                            if total > 0 and (_last_round_seen[1] or 0) <= 0:
                                self._recompute_dynamic_bands(state, total_rounds=total)  # T6.7
                                self._update_manifest(state, STAGE_RUN, total_rounds=total)  # I-8-1
                            _last_round_seen = (cur, total)
                            _last_progress_at = time.monotonic()  # B12: 有进展即续命看门狗
                            if total > 0:
                                upd(min(98, int(cur / total * 100)), f"模拟轮次 {cur}/{total}")
                            else:
                                upd(5, "模拟进行中…")
                        if rs.runner_status == RunnerStatus.COMPLETED:
                            break
                        if rs.runner_status in (RunnerStatus.FAILED, RunnerStatus.STOPPED):
                            raise RuntimeError(f"模拟未正常结束: {rs.runner_status} {getattr(rs, 'error', '') or ''}")
                        # B12: 停滞看门狗——长时间无轮次推进（区别于「慢但在推进」）判定为卡死，
                        # 停模拟并失败，避免管线线程永久空转。取消仍由循环顶部兜底；默认 30min。
                        if _stall_s > 0 and (time.monotonic() - _last_progress_at) > _stall_s:
                            try:
                                SimulationRunner.stop_simulation(sim_state.simulation_id)
                            except Exception as _wd_err:  # noqa: BLE001
                                logger.warning(f"[{state.pipeline_id}] 看门狗停止模拟失败: {_wd_err}")
                            raise RuntimeError(f"模拟约 {int(_stall_s)}s 无进展（疑似卡死），看门狗已终止")
                        time.sleep(5)
                finally:
                    _wd_ctl["stop"] = True  # C1/C2/ORCH-6: 任何退出路径都退休独立看门狗
                # 同步 SimulationManager 状态
                try:
                    ss = sim_manager.get_simulation(sim_state.simulation_id)
                    if ss is not None:
                        ss.status = SimulationStatus.COMPLETED
                        sim_manager._save_simulation_state(ss)
                except Exception:
                    pass

                # EXECPLAN2 F-12-1: 模拟结束后、读 actions.jsonl 写 run_summary 与跑报告之前，
                # 加一道"汇流栅栏"。runner_status 会在 simulation_end 事件被解析的瞬间就置为
                # COMPLETED（此时 OASIS 进程仍存活、监控线程仍在收尾，「模拟 → 图谱」反馈写入器
                # 也可能还在排空队列向同一张 FalkorDB 图谱写 typed 边/episode）。若直接前进，报告
                # 可能读到只摄入了一半反馈的图谱、run_summary 也可能基于仍在增长的 actions.jsonl，
                # 导致同一模拟的报告 run-to-run 漂移、漏掉末轮反馈动态。故：
                #   (1) join 监控线程 → 保证 OASIS 进程已退出且 _monitor_simulation 的 finally 跑完；
                #   (2) 显式 stop_updater → 其 stop() 会 _flush_remaining 再 join worker（幂等，
                #       即便监控线程已停过也安全）。
                # 仅在本次运行启用了反馈回路时才需要（与 RUN 启动条件一致）；任何卡顿降级为告警，
                # 不让栅栏本身拖垮管线。
                if Config.SIM_GRAPH_FEEDBACK and graph_id:
                    # F-12-1 汇流栅栏：经公共访问点等待监控线程退出（不再直接读私有注册表）。
                    if not SimulationRunner.join_monitor_thread(sim_state.simulation_id, timeout=30):
                        logger.warning(
                            "[%s] 等待模拟监控线程退出超时（降级继续）", state.pipeline_id,
                        )
                    try:
                        ZepGraphMemoryManager.stop_updater(sim_state.simulation_id)
                    except Exception as _flush_err:  # noqa: BLE001
                        logger.warning(
                            "[%s] 排空图谱反馈写入器失败（降级继续）: %s",
                            state.pipeline_id, _flush_err,
                        )

                self._complete_stage(state, STAGE_RUN, "模拟完成")

            # T3.14: 模拟结束后聚合 run_summary.json（per-agent engagement + 每轮动作量 + top_posts +
            # 可选派系）。报告阶段的 simulation_outcomes 工具与前端均可直接读，免再 fuzzy 检索。
            try:
                _comm = None
                _comm_path = os.path.join(handoff_dir, "communities.json")
                if os.path.exists(_comm_path):
                    with open(_comm_path, "r", encoding="utf-8") as cf:
                        _comm = json.load(cf)
                SimulationRunner.write_run_summary(sim_state.simulation_id, communities=_comm)
                _rs = os.path.join(SimulationRunner.RUN_STATE_DIR, sim_state.simulation_id, "run_summary.json")
                if os.path.exists(_rs) and os.path.getsize(_rs) > 0:
                    state.artifacts["run_summary"] = _rs  # T6.3
                    PipelineManager.save(state)
                    # I-4-3: run_summary 在 _complete_stage(RUN) 之后才落盘，故补登清单条目，
                    # 让后续 resume 能对它做完整性校验（否则清单缺该条 → 校验放行无保障）。
                    if bool(getattr(Config, "PIPELINE_VALIDATE_ARTIFACTS", True)):
                        try:
                            _man = PipelineManager.load_artifact_manifest(state.pipeline_id)
                            _entry = _manifest_entry_for("run_summary", _rs, STAGE_RUN)
                            if _entry is not None:
                                _man["run_summary"] = _entry
                                PipelineManager.write_artifact_manifest(state.pipeline_id, _man)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] run_summary 写出跳过: %s", state.pipeline_id, e)

            # ---- Stage 5: REPORT ----
            upd = self._make_stage_updater(state, STAGE_REPORT)
            self._update_manifest(state, STAGE_REPORT)  # I-8-1
            # EXECPLAN2 F-1-0: REPORT 复用守卫——以"已落盘的报告本身"为复用信号，而非
            # stage 状态。REPORT 总是末阶段：当报告已生成、却在收尾 DONE 记账处崩溃/失败时，
            # _fail_stage / mark_failed 会把 REPORT（current_stage）状态翻成 failed，resume()
            # 又把它重置为 pending——因此 state.stages[REPORT].status 在恢复时永远不是 completed，
            # 无法像其它阶段那样用阶段状态做复用判定。改为：若 state.report_id 能解析到一份非
            # FAILED 的已存报告，则直接复用（保留原 report_id，不再凭空铸新 id 孤立前端/书签链接），
            # 避免重复跑最贵的多工具 LLM 报告生成。
            existing_report = None
            if state.report_id:
                try:
                    existing_report = ReportManager.get_report(state.report_id)
                except Exception:
                    existing_report = None
            # 兜底：report_id 丢失但该模拟已有报告时，按 simulation_id 找回（同样保留原 id）。
            if existing_report is None and sim_state is not None:
                try:
                    existing_report = ReportManager.get_report_by_simulation(sim_state.simulation_id)
                except Exception:
                    existing_report = None
            # ORCH-1: 复用前评估交付物本身。meta 说 COMPLETED 但全章占位/无 forecast.json 的
            # 报告若被复用，S1 健康门必再抛错 → resume 陷入「复用坏报告→健康门失败」死循环
            # （report_id=None 手工修复也不够：get_report_by_simulation 兜底会把它找回来）。
            # 交付物判 failed → 弃用复用、落到重建分支铸新 report_id。PIPELINE_HEALTH_GATE 关闭时不改行为。
            if (existing_report is not None
                    and getattr(Config, "PIPELINE_HEALTH_GATE", True)):
                _cand_rid = getattr(existing_report, "report_id", None) or state.report_id
                try:
                    _erh, _eri, _ = self._assess_report_health(_cand_rid)
                except Exception:  # noqa: BLE001 — 评估异常不拦一次合法复用
                    _erh, _eri = "ok", []
                if _erh == "failed":
                    logger.warning("[%s] 现有报告 %s 交付物损坏（%s），弃用复用、重建报告",
                                   state.pipeline_id, _cand_rid, "；".join(_eri)[:200])
                    state.options["report_rebuilt_broken_deliverable"] = _cand_rid
                    existing_report = None
            # ORCH-3(b): force resume 显式要求重生成报告 → 跳过复用（一次性标记，用后即清）。
            if existing_report is not None and state.options.pop("force_report_regen", None):
                logger.info("[%s] force resume：跳过报告复用，重生成", state.pipeline_id)
                existing_report = None
            if existing_report is not None and getattr(existing_report, "status", None) != ReportStatus.FAILED:
                upd(100, "复用已有报告")
                state.report_id = getattr(existing_report, "report_id", state.report_id)
                self._complete_stage(state, STAGE_REPORT, "报告完成（复用）")
            else:
                # ORCH-8: 报告是最贵的 LLM 阶段，而健康门在全部章节成本烧完后才触发。双 provider
                # 同时不可用（MiniMax 审查/配额 + claude-cli 耗尽）时，先花 ~10 token 探测一次
                # （chat() 自带重试+回退链）；失败即在 <60s 内以可恢复的 REPORT 阶段失败收场，
                # 而不是磨完 N 个占位章节。带 uuid 防命中 LLM_CACHE 的陈旧 'ping' 结果。
                if bool(getattr(Config, "REPORT_LLM_PREFLIGHT", True)):
                    try:
                        from ..utils.llm_client import LLMClient as _PreflightLLM
                        _PreflightLLM().chat(
                            [{"role": "user", "content": f"ping {uuid.uuid4().hex[:8]} — reply: pong"}],
                            temperature=0.0, max_tokens=8,
                        )
                    except Exception as _pf_err:  # noqa: BLE001
                        raise RuntimeError(
                            "报告前置探测失败：主/回退 LLM 提供方均不可用 —— 中止报告阶段以免"
                            f"烧掉全部章节成本（稍后 resume 可从 REPORT 续跑）: {str(_pf_err)[:200]}"
                        )
                # XRUN-15: 铸新报告 = 新 attempt，清掉上一 attempt 的 *_partial 临时产物指针
                # （它们指向被取代的旧 report/sim 目录，读者会拼出 Franken-run）。扫描器会为新
                # attempt 重新登记。
                for _pk in [k for k in list(state.artifacts) if k.endswith("_partial")]:
                    state.artifacts.pop(_pk, None)
                upd(5, "生成预测报告…")
                report_id = f"report_{uuid.uuid4().hex[:12]}"
                # T4.6/T4.7: 情景报告 → 传情景标签 + base 模拟 id（反事实对比 scenario_diff）
                _scenario_label = state.options.get("scenario_label")
                _base_sim_id = None
                _base_pid = state.options.get("base_pipeline_id")
                if _base_pid:
                    try:
                        _bd = PipelineManager.load(_base_pid) or {}
                        _base_sim_id = _bd.get("simulation_id")
                    except Exception:
                        _base_sim_id = None
                # Visual ownership boundary: research charts remain relative to the
                # dossier handoff. The final report consumes the shared structured
                # artifacts below and ReportVisualizer generates its own richer,
                # report-owned Plotly set. Passing research paths into every section
                # prompt would duplicate timeline/actor/quantitative figures.
                _ra_kwargs: dict[str, Any] = dict(
                    graph_id=graph_id,
                    simulation_id=sim_state.simulation_id,
                    simulation_requirement=state.prompt,
                    # T4.1: 钉入研究档案，报告不再从冷图盲搜重挖 cast/关系/时间线
                    situation_brief=situation_brief(actors),
                    actors=actors,
                    sources=research.get("sources"),
                    research_report=report_md,
                    scenario_label=_scenario_label,
                    base_simulation_id=_base_sim_id,
                )
                # W9-8: 研究昂贵产物直通报告链——quantitative(339 行)/contested(29 条)/
                # timeline(101 事件)/graph_priors(_structural) 此前落盘后零下游读者。
                # 构造参数由报告链工作流并行落地（None 默认）；尚未支持时 TypeError →
                # 回退旧签名（落地顺序无关）。缺文件时 _read_json 返回 None（同 None 默认）。
                _hd_ra = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                _ra_artifacts: dict[str, Any] = {
                    "quantitative": _read_json(os.path.join(_hd_ra, "quantitative.json")),
                    "contested": _read_json(os.path.join(_hd_ra, "contested.json")),
                    "timeline_events": _read_json(os.path.join(_hd_ra, "timeline.json")),
                    "graph_priors": _read_json(os.path.join(_hd_ra, "graph_priors.json")),
                    "graph_priors_structural": _read_json(
                        os.path.join(_hd_ra, "graph_priors_structural.json")),
                }
                try:
                    agent = ReportAgent(**_ra_kwargs, **_ra_artifacts)
                except TypeError:
                    logger.info("[%s] ReportAgent 尚未支持研究工件直通参数，回退旧签名",
                                state.pipeline_id)
                    agent = ReportAgent(**_ra_kwargs)

                def report_cb(stage: str, progress: int, message: str):
                    upd(max(5, min(99, int(progress))), f"{stage}: {message}")

                report = agent.generate_report(progress_callback=report_cb, report_id=report_id)
                try:
                    ReportManager.save_report(report)
                except Exception:
                    pass
                state.report_id = getattr(report, "report_id", report_id)
                if getattr(report, "status", None) == ReportStatus.FAILED:
                    raise RuntimeError(getattr(report, "error", "报告生成失败"))
                self._complete_stage(state, STAGE_REPORT, "报告完成")

            # ORCH-4: NEXTSTEPS P0-3 多种子集成——此前实现完整却从未被调用（N_FORECAST_SEEDS>1
            # 静默 no-op）。在主报告完成后、健康门之前接线，使集成的信心改写落进被健康门校验的
            # forecast.json。方法自身在 N_FORECAST_SEEDS<=1（默认）时直接返回，默认行为逐字节不变。
            if state.mode == "full":
                try:
                    self._maybe_run_seed_ensemble(state, project, graph_id, actors, research, report_md)
                except PipelineCancelled:
                    raise
                except Exception as _ens_err:  # noqa: BLE001 — 集成失败不拖垮已成功的主跑
                    logger.warning("[%s] 多种子集成失败（主报告不受影响）: %s",
                                   state.pipeline_id, _ens_err)

            # ---- DONE ---- S1: validate the real deliverable before declaring success.
            # Hard-fails (raises → status=failed) on an empty/placeholder report or missing
            # forecast.json; records a degraded health block otherwise. Makes broken runs visible.
            self._enforce_pipeline_health(state)
            state.status = "completed"
            state.global_progress = 100
            PipelineManager.save(state)
            if state.task_id:
                task_manager.complete_task(state.task_id, result={
                    "pipeline_id": state.pipeline_id,
                    "project_id": state.project_id,
                    "graph_id": state.graph_id,
                    "simulation_id": state.simulation_id,
                    "report_id": state.report_id,
                })
            logger.info(f"[{state.pipeline_id}] 全流程完成 report={state.report_id}")

        except PipelineCancelled as e:
            logger.info(f"[{state.pipeline_id}] 管线已取消: {e}")
            state.status = "cancelled"
            state.error = str(e)
            if state.current_stage:
                st = state.stages.setdefault(state.current_stage, StageState(name=state.current_stage))
                # XRUN-15: 已完成（progress=100）的阶段是完成的工作，取消只作用于未完成阶段，
                # 避免出现 status='cancelled' + progress=100 + '…完成' 的自相矛盾标签。
                if not (st.status == "completed" or (st.progress or 0) >= 100):
                    st.status = "cancelled"
                    st.error = str(e)
                st.finished_at = st.finished_at or _utcnow()
            PipelineManager.save(state)
            if state.task_id:
                try:
                    task_manager.fail_task(state.task_id, str(e))
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"[{state.pipeline_id}] 管线失败: {e}", exc_info=True)
            state.status = "failed"
            state.error = str(e)
            if state.current_stage:
                self._fail_stage(state, state.current_stage, str(e))
            PipelineManager.save(state)
            if state.task_id:
                try:
                    task_manager.fail_task(state.task_id, str(e))
                except Exception:
                    pass
        finally:
            # I-4-1: 停掉心跳看护线程（管线已到终态，不应再刷新 heartbeat_at）。
            if hb_stop is not None:
                hb_stop.set()
            # EXECPLAN2 I-5-1: 落盘本次管线的 LLM 计量（token/成本/延迟，按阶段/模型），便于复盘。
            try:
                tpath = os.path.join(PipelineManager._dir(state.pipeline_id), "run_telemetry.json")
                _tel_extra: dict[str, Any] = {
                    "pipeline_id": state.pipeline_id,
                    "status": state.status,
                    "report_id": state.report_id,
                }
                # XRUN-8: 折入报告自身的 telemetry.json rollup——复用报告时 by_stage.report 只有
                # 1 次 cached/0 token 的缓存命中，报告的真实成本在它自己的目录里。
                try:
                    if state.report_id:
                        _rt = _read_json(os.path.join(
                            ReportManager._get_report_folder(state.report_id), "telemetry.json"))
                        if isinstance(_rt, dict):
                            _tel_extra["report_telemetry"] = _rt
                except Exception:  # noqa: BLE001
                    pass
                # XRUN-8(2): RUN 阶段的 LLM 调用发生在 sim 子进程（contextvars 不跨进程），
                # 折入其自报的 llm_health.json 平台级计数，避免 run 阶段在 rollup 里显示为 0。
                try:
                    if state.simulation_id:
                        _lh = _read_json(os.path.join(
                            Config.OASIS_SIMULATION_DATA_DIR, state.simulation_id, "llm_health.json"))
                        if isinstance(_lh, dict):
                            _tel_extra["sim_llm_health"] = _lh
                except Exception:  # noqa: BLE001
                    pass
                # W9-3: 终版落盘走增量 flush 的同一路径——合并基底在 attempt 起点捕获
                # （_init_telemetry_flush），与运行中的多次 flush 不会重复累计 cumulative_total。
                # （旧的 LLMMeter.write_run_telemetry 每次调用都把盘上文件当「上一 attempt」
                # 合并，与增量 flush 组合会把本 attempt 的中间快照重复累进账里。）
                self._tel_path = self._tel_path or tpath
                self._flush_run_telemetry(state, final=True, extra=_tel_extra)
                state.artifacts = getattr(state, "artifacts", None) or {}
                if isinstance(state.artifacts, dict):
                    state.artifacts["run_telemetry"] = tpath
                    PipelineManager.save(state)
                # ITEM-18: 阶段级遥测（stage × 调用/tokens/成本/墙钟）。把 LLMMeter 的 per-stage 快照
                # 与编排器记录的每阶段墙钟合并，落 telemetry.json（区别于 run_telemetry.json 的原始计量
                # 转储），把紧凑摘要折入 state.options.llm_telemetry（经 GET /status 返回 options 暴露给
                # 前端），并在成功完成时把确定性「Run Telemetry」附录追加到最终报告。必须在 reset 前算——
                # 用独立 try/except 兜底，任何失败都不得跳过下方的 LLMMeter.reset。
                try:
                    from ..utils.atomic import write_json_atomic, write_text_atomic
                    from ..utils.telemetry import build_stage_telemetry, render_telemetry_appendix
                    _stage_tel = build_stage_telemetry(state.pipeline_id, _stage_walls(state))
                    try:
                        _stpath = os.path.join(
                            PipelineManager._dir(state.pipeline_id), "telemetry.json")
                        write_json_atomic(_stpath, _stage_tel)
                        if isinstance(state.artifacts, dict):
                            state.artifacts["telemetry"] = _stpath
                    except Exception as _swe:  # noqa: BLE001 — 工件落盘失败不影响摘要/附录
                        logger.debug(f"[{state.pipeline_id}] 写入 telemetry.json 失败（忽略）: {_swe}")
                    # 只把紧凑摘要折入 options（避免状态文件因 per-model 明细膨胀）
                    state.options["llm_telemetry"] = {
                        "total": _stage_tel.get("total"),
                        "by_stage": _stage_tel.get("by_stage"),
                        "cost_estimated": _stage_tel.get("cost_estimated"),
                        "cost_basis": _stage_tel.get("cost_basis"),
                    }
                    PipelineManager.save(state)
                    # W9-6: 遥测默认不再进入客户交付物——「Run Telemetry」表写到报告目录的
                    # 独立 telemetry.md（$26.28 的内部成本表曾随 full_report.md 一并交付）。
                    # REPORT_TELEMETRY_APPENDIX=true 时额外保留旧的 full_report.md 附录行为
                    # （幂等：已含则跳过，resume/re-report 安全）。
                    if state.status == "completed" and state.report_id:
                        _appendix = render_telemetry_appendix(_stage_tel)
                        if _appendix:
                            _rdir = ReportManager._get_report_folder(state.report_id)
                            try:
                                write_text_atomic(
                                    os.path.join(_rdir, "telemetry.md"),
                                    _appendix.rstrip() + "\n")
                            except Exception as _tme:  # noqa: BLE001 — telemetry.md 纯观测
                                logger.debug(
                                    f"[{state.pipeline_id}] 写入 telemetry.md 失败（忽略）: {_tme}")
                            if getattr(Config, "REPORT_TELEMETRY_APPENDIX", False):
                                _rpath = os.path.join(_rdir, "full_report.md")
                                _existing = _read_text(_rpath)
                                if _existing and "## Run Telemetry" not in _existing:
                                    write_text_atomic(
                                        _rpath, _existing.rstrip() + "\n\n" + _appendix.rstrip() + "\n")
                except Exception as _ste:  # noqa: BLE001 — 阶段遥测为观测增益，失败不影响管线终态
                    logger.debug(f"[{state.pipeline_id}] 阶段级遥测处理失败（忽略）: {_ste}")
                LLMMeter.reset(state.pipeline_id)
            except Exception as _te:
                logger.debug(f"[{state.pipeline_id}] 写入 run_telemetry 失败（忽略）: {_te}")
            # 线程结束即从注册表移除，避免 _threads 无界增长，并让 reconcile_orphans 的
            # "pid in _threads" 判定准确反映当前在飞的线程。
            cls._threads.pop(state.pipeline_id, None)
            cls._cancel_events.pop(state.pipeline_id, None)
