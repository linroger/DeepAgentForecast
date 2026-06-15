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
import json
import os
import re
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

    @classmethod
    def save(cls, state: PipelineState) -> None:
        cls.ensure_dirs(state.pipeline_id)
        state.updated_at = _utcnow()
        # I-4-4: 每次落盘都写当前 schema 版本（即便 dataclass 实例由旧文件迁移而来）。
        state.schema_version = PIPELINE_SCHEMA_VERSION
        tmp = cls.state_path(state.pipeline_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, cls.state_path(state.pipeline_id))

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
        data = cls.load(pipeline_id)
        if not data:
            return False
        data["status"] = status
        data["error"] = error
        data["updated_at"] = _utcnow()
        cur = data.get("current_stage")
        stages = data.get("stages") or {}
        if cur and isinstance(stages.get(cur), dict):
            stages[cur]["status"] = status
            stages[cur]["error"] = error
        cls.ensure_dirs(pipeline_id)
        tmp = cls.state_path(pipeline_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cls.state_path(pipeline_id))
        return True

    @classmethod
    def touch_heartbeat(cls, pipeline_id: str, pid: Optional[int] = None) -> bool:
        """I-4-1: 仅刷新 heartbeat_at（+ 可选 owner_pid/owner_boot_id），不重建 dataclass。

        独立于阶段进度的「我还活着」壁钟信号，由 _run 的看护线程按固定节律调用。沿用
        mark_failed 的轻量直写模式（load → 改两三个键 → 原子替换），避免每次心跳走 full save
        / 触发 schema 迁移副作用。状态非 running 时静默 no-op（终态管线无需心跳）。
        """
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
        tmp = cls.state_path(pipeline_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cls.state_path(pipeline_id))
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
    ) -> dict[str, Any]:
        """运行研究子进程，阻塞直到结束。返回 handoff 摘要。

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

        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        # T6.6: deep 开场 pass 的递归上限从 Config 单一真源下发给子进程（旧版在 bridge 内直接读
        # os.environ）。Config 属性本身读 env（默认 220），故此处覆盖即「Config 即真源」。
        env["DEERFLOW_DEEP_OPENING_RECURSION_LIMIT"] = str(Config.DEERFLOW_DEEP_OPENING_RECURSION_LIMIT)

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
        # 用户在 .env 里显式设置的 DEERFLOW_RESEARCH_TIMEOUT > Config.DEERFLOW_DEPTH_BUDGETS 档位默认值。
        effective_depth = (depth or Config.DEERFLOW_RESEARCH_DEPTH or "standard").lower()
        if timeout:
            budget = timeout
        elif os.environ.get("DEERFLOW_RESEARCH_TIMEOUT", "").strip():
            budget = Config.DEERFLOW_RESEARCH_TIMEOUT
        else:
            budget = Config.DEERFLOW_DEPTH_BUDGETS.get(effective_depth, Config.DEERFLOW_RESEARCH_TIMEOUT)
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
            returncode = proc.wait(timeout=30)
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
        _err_markers = (
            "The configured LLM provider",  # DeerFlow LLMErrorHandlingMiddleware 降级
            "LLM request failed",            # 原始 provider 报错被当成正文
            "unprocessable_entity",          # 例如 MiniMax 422 内容审核
            "new_sensitive",                 # MiniMax 域内容过滤命中(code 1026)
            "Error code: 4", "Error code: 5",  # 4xx/5xx 错误串
        )
        if len(report.strip()) < 400 and any(m in report for m in _err_markers):
            raise RuntimeError(
                "DeerFlow 返回的是 LLM 降级/错误消息而非研究报告"
                "（提供方临时不可用/限流/额度、网络错误，或内容审核拦截），"
                "请稍后重试、降低研究深度，或更换模型"
            )

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
    return {
        "report": report,
        "report_path": report_path,
        "actors": _read_json(os.path.join(handoff_dir, "actors.json")),
        "sources": _read_json(os.path.join(handoff_dir, "sources.json")),
        "timeline": _read_json(os.path.join(handoff_dir, "timeline.json")),
        "exit_code": None,
        "resumed": True,
    }


def load_research_dossier_for_simulation(simulation_id: Optional[str]) -> dict[str, Any]:
    """T4.1: best-effort 找到含该 simulation_id 的管线 handoff，加载研究档案。

    供手动报告路径（api/report.py）复用：让手动生成/对话也能钉入背景档案。找不到对应
    管线 / handoff 时全部返回 None，ReportAgent 回退到冷图盲搜路径（与旧行为一致）。
    """
    out: dict[str, Any] = {
        "situation_brief": None, "actors": None, "sources": None, "research_report": None,
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
            out["actors"] = actors
            out["sources"] = _read_json(os.path.join(hd, "sources.json"))
            out["research_report"] = report or None
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
                msg = "后端在运行中被中断（进程重启），该管线已标记为失败。"
                if PipelineManager.mark_failed(pipeline_id, msg):
                    logger.warning(f"[{pipeline_id}] 启动时回收孤儿管线 → failed")
                    data = PipelineManager.load(pipeline_id) or {}
                    # 杀掉上一进程遗留、仍在烧额度的孤儿研究子进程（按持久化的 PID）。
                    cls._kill_orphan_research(pipeline_id, data.get("research_pid"))
                    tid = data.get("task_id")
                    if tid:
                        try:
                            task_manager.fail_task(tid, msg)
                        except Exception:
                            pass
        except Exception as e:  # noqa: BLE001 — 回收失败不应阻断启动
            logger.error(f"回收孤儿管线失败: {e}", exc_info=True)

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
    def delete_pipeline(cls, pipeline_id: str) -> dict[str, Any]:
        """删除一条已结束的管线记录（含其 handoff 产物目录）。

        在飞管线必须先取消再删除——删除运行中的状态文件会让 _run 线程在下次
        落盘时凭空复活记录，且孤儿子进程无人回收。

        Returns:
            {"ok": bool, "status": str}  status ∈ deleted / not_found / still_running
        """
        with cls._lifecycle_lock:
            live = cls._threads.get(pipeline_id)
            if live is not None and live.is_alive():
                return {"ok": False, "status": "still_running"}
            data = PipelineManager.load(pipeline_id)
            if data is None:
                return {"ok": False, "status": "not_found"}
            if data.get("status") == "running":
                # 持久化为 running 但本进程无线程 → 孤儿；先按取消语义落盘再删，
                # 这样即使删除中途失败，状态也不会停在 running 误导前端。
                PipelineManager.mark_failed(pipeline_id, "已被用户删除", status="cancelled")
                cls._kill_orphan_research(pipeline_id, data.get("research_pid"))
            ok = PipelineManager.delete(pipeline_id)
            if ok:
                cls._cancel_events.pop(pipeline_id, None)
                cls._threads.pop(pipeline_id, None)
                logger.info(f"[{pipeline_id}] 管线记录已删除")
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
    def resume(cls, pipeline_id: str) -> PipelineState:
        """Resume a failed/cancelled pipeline in place, reusing existing artifacts.

        The pipeline keeps the same id so browser history, artifact paths, and
        local bookmarks remain valid. A fresh task id is assigned for progress
        polling, and the background runner skips completed/recoverable stages.
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
                raise RuntimeError("管线仍在运行，无法恢复")
            if data.get("status") == "completed":
                raise RuntimeError("管线已完成，无需恢复")

            state = PipelineState.from_dict(data)
            PipelineManager.ensure_dirs(pipeline_id)
            bands = RESEARCH_ONLY_BANDS if state.mode == "research_only" else STAGE_BANDS
            for name in bands.keys():
                state.stages.setdefault(name, StageState(name=name))

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
            pass
        return specs

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

    # -- 内部：主流程 ------------------------------------------------------

    @classmethod
    def _run(cls, state: PipelineState) -> None:
        self = cls()
        task_manager = TaskManager()
        # EXECPLAN2 I-5-0/I-5-2: 把本次管线所有 LLM 调用归属到该 pipeline_id（contextvars）。
        from ..utils.telemetry import set_run_context, LLMMeter
        set_run_context(state.pipeline_id)
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

                research = DeerFlowResearchRunner.run(
                    state.prompt,
                    handoff_dir,
                    on_progress=upd,
                    depth=state.options.get("depth"),
                    language=state.options.get("research_language"),  # T5.5
                    model=state.options.get("research_model"),        # T5.5
                    cancel_event=cls._cancel_events.get(state.pipeline_id),
                    on_spawn=_persist_research_pid,
                )
                state.research_pid = None  # 子进程已结束，清掉以免 reconcile 误杀复用 PID
                self._complete_stage(state, STAGE_RESEARCH, "研究完成")
            report_md: str = research["report"]
            actors = research.get("actors")
            # I-5-7: 把研究阶段遥测并入统一计量（stash 到 options + 喂给 meter）。
            self._record_research_telemetry(state, research.get("research_telemetry"))
            # I-0-3: 透传研究覆盖度/质量记分牌（meta.json → options，纯观测，永不硬失败）。
            self._surface_research_quality(state, handoff_dir)

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
                ontology = generator.generate(
                    document_texts=[report_md],
                    simulation_requirement=state.prompt,
                    additional_context=_actors_to_context(actors),
                )
                project.ontology = {
                    "entity_types": ontology.get("entity_types", []),
                    "edge_types": ontology.get("edge_types", []),
                }
                project.analysis_summary = ontology.get("analysis_summary", "")
                project.status = ProjectStatus.ONTOLOGY_GENERATED
                ProjectManager.save_project(project)
                # T6.3: 把本体落到 handoff/ontology.json，供 artifact 深链
                try:
                    _hd = state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id)
                    with open(os.path.join(_hd, "ontology.json"), "w", encoding="utf-8") as _of:
                        json.dump(project.ontology, _of, ensure_ascii=False, indent=2)
                except Exception:
                    pass
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
                self._complete_stage(state, STAGE_GRAPH, "图谱已恢复")
            else:
                upd(5, "构建知识图谱…")
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                chunks = TextProcessor.split_text(report_md, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
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
                PipelineManager.save(state)

                def prepare_cb(stage: str, progress: int, message: str, **_kwargs):
                    upd(max(5, min(99, int(progress))), f"{stage}: {message}")

                # persona 生成并发：CLI 提供方受本机 CLI 吞吐限制保持 3；
                # OpenAI 兼容 HTTP 提供方可以放心放大（每个 persona 1 次 LLM + 2 次 Zep 检索）。
                _is_http_provider = bool(Config.PROVIDER_META.get(Config.LLM_PROVIDER, {}).get('openai_compat'))
                sim_manager.prepare_simulation(
                    simulation_id=sim_state.simulation_id,
                    simulation_requirement=state.prompt,
                    document_text=report_md,
                    progress_callback=prepare_cb,
                    parallel_profile_count=8 if _is_http_provider else 3,
                    actors=actors,  # 研究档案直通模拟准备：persona/配置以实证立场为准
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
                        if total > 0:
                            upd(min(98, int(cur / total * 100)), f"模拟轮次 {cur}/{total}")
                        else:
                            upd(5, "模拟进行中…")
                    if rs.runner_status == RunnerStatus.COMPLETED:
                        break
                    if rs.runner_status in (RunnerStatus.FAILED, RunnerStatus.STOPPED):
                        raise RuntimeError(f"模拟未正常结束: {rs.runner_status} {getattr(rs, 'error', '') or ''}")
                    time.sleep(5)
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
                    try:
                        _mon = SimulationRunner._monitor_threads.get(sim_state.simulation_id)
                        if _mon is not None and _mon.is_alive():
                            _mon.join(timeout=30)
                    except Exception as _join_err:  # noqa: BLE001
                        logger.warning(
                            "[%s] 等待模拟监控线程退出失败（降级继续）: %s",
                            state.pipeline_id, _join_err,
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
            if existing_report is not None and getattr(existing_report, "status", None) != ReportStatus.FAILED:
                upd(100, "复用已有报告")
                state.report_id = getattr(existing_report, "report_id", state.report_id)
                self._complete_stage(state, STAGE_REPORT, "报告完成（复用）")
            else:
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
                agent = ReportAgent(
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

            # ---- DONE ----
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
                st.status = "cancelled"
                st.error = str(e)
                st.finished_at = _utcnow()
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
                LLMMeter.write_run_telemetry(tpath, run_id=state.pipeline_id, extra={
                    "pipeline_id": state.pipeline_id,
                    "status": state.status,
                    "report_id": state.report_id,
                })
                state.artifacts = getattr(state, "artifacts", None) or {}
                if isinstance(state.artifacts, dict):
                    state.artifacts["run_telemetry"] = tpath
                    PipelineManager.save(state)
                LLMMeter.reset(state.pipeline_id)
            except Exception as _te:
                logger.debug(f"[{state.pipeline_id}] 写入 run_telemetry 失败（忽略）: {_te}")
            # 线程结束即从注册表移除，避免 _threads 无界增长，并让 reconcile_orphans 的
            # "pid in _threads" 判定准确反映当前在飞的线程。
            cls._threads.pop(state.pipeline_id, None)
            cls._cancel_events.pop(state.pipeline_id, None)
