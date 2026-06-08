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
  sibling 仓库的 ``deerflow_research.py``，消费其写出的文件化 handoff 契约。
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
import signal
import subprocess
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


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageState:
    name: str
    status: str = "pending"          # pending / running / completed / failed / skipped
    progress: int = 0                # 0-100 (阶段内)
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PipelineState:
    pipeline_id: str
    prompt: str
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
    error: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    options: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, StageState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# 管线状态持久化（file-backed，沿用 MiroFish 的目录约定）
# ---------------------------------------------------------------------------


class PipelineManager:
    """读写 uploads/pipelines/<id>/pipeline_state.json。"""

    @classmethod
    def _dir(cls, pipeline_id: str) -> str:
        return os.path.join(Config.PIPELINE_DATA_DIR, pipeline_id)

    @classmethod
    def state_path(cls, pipeline_id: str) -> str:
        return os.path.join(cls._dir(pipeline_id), "pipeline_state.json")

    @classmethod
    def handoff_dir(cls, pipeline_id: str) -> str:
        return os.path.join(cls._dir(pipeline_id), "handoff")

    @classmethod
    def ensure_dirs(cls, pipeline_id: str) -> None:
        os.makedirs(cls.handoff_dir(pipeline_id), exist_ok=True)

    @classmethod
    def save(cls, state: PipelineState) -> None:
        cls.ensure_dirs(state.pipeline_id)
        state.updated_at = _utcnow()
        tmp = cls.state_path(state.pipeline_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, cls.state_path(state.pipeline_id))

    @classmethod
    def mark_failed(cls, pipeline_id: str, error: str) -> bool:
        """直接在持久化 JSON 上把管线标记为 failed（无需重建 dataclass）。

        用于启动时回收孤儿管线：进程崩溃/重启后，pipeline_state.json 可能永远停在
        running，前端轮询据此空转。原子写入（tmp + os.replace），同时把当前阶段标失败。
        """
        data = cls.load(pipeline_id)
        if not data:
            return False
        data["status"] = "failed"
        data["error"] = error
        data["updated_at"] = _utcnow()
        cur = data.get("current_stage")
        stages = data.get("stages") or {}
        if cur and isinstance(stages.get(cur), dict):
            stages[cur]["status"] = "failed"
            stages[cur]["error"] = error
        cls.ensure_dirs(pipeline_id)
        tmp = cls.state_path(pipeline_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cls.state_path(pipeline_id))
        return True

    @classmethod
    def load(cls, pipeline_id: str) -> Optional[dict[str, Any]]:
        path = cls.state_path(pipeline_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

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
    ) -> dict[str, Any]:
        """运行研究子进程，阻塞直到结束。返回 handoff 摘要。

        Raises:
            RuntimeError: 子进程失败、超时或未产出报告。
        """
        deerflow_dir = Config.DEERFLOW_DIR
        script = os.path.join(deerflow_dir, "deerflow_research.py")
        if not os.path.isdir(deerflow_dir):
            raise RuntimeError(f"DeerFlow 目录不存在: {deerflow_dir}（设置 DEERFLOW_DIR）")
        if not os.path.exists(script):
            raise RuntimeError(f"未找到 deerflow_research.py: {script}")

        os.makedirs(handoff_dir, exist_ok=True)
        cmd = _detect_deerflow_python(deerflow_dir) + [
            script,
            "--prompt", prompt,
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

        budget = timeout or Config.DEERFLOW_RESEARCH_TIMEOUT
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

        # 启发式进度：研究阶段难以精确，按事件类型缓慢推进 2→95。
        local = 2
        tool_events = 0
        last_line = ""
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                if not line:
                    continue
                last_line = line
                # 解析进度日志的事件类型 [tool]/[result]/[stage]/[ok]/[done]/[error]
                if "[tool]" in line:
                    tool_events += 1
                    local = min(90, 10 + tool_events * 4)
                    on_progress(local, _tail(line))
                elif "[result]" in line:
                    on_progress(local, _tail(line))
                elif "[stage]" in line:
                    on_progress(min(local, 92), _tail(line))
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

        if timed_out["hit"]:
            raise RuntimeError(f"DeerFlow 研究超时（>{budget}s）")

        report_path = os.path.join(handoff_dir, "research_report.md")
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
        on_progress(100, f"研究完成（报告 {len(report)} 字）")
        return {
            "report": report,
            "report_path": report_path,
            "actors": actors,
            "sources": sources,
            "exit_code": returncode,
        }


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
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """串联 research → ontology → graph → prepare → run → report。"""

    _threads: dict[str, threading.Thread] = {}
    _cleanup_registered: bool = False

    # -- 生命周期：启动回收 + 关闭清理 ------------------------------------

    @classmethod
    def reconcile_orphans(cls) -> None:
        """后端启动时回收孤儿管线。

        硬杀 / 崩溃 / 重启会跳过 ``_run`` 的 except 块，使 pipeline_state.json 永远停在
        ``running``；前端 ``poll()`` 只在 completed/failed 时停止，于是无限空转。进程刚启动时
        ``_threads`` 必为空，故任何持久化为 running 的管线都是上一进程遗留的孤儿 → 标记 failed。
        """
        try:
            from ..models.task import TaskManager
            task_manager = TaskManager()
            for p in PipelineManager.list_pipelines():
                if p.get("status") != "running":
                    continue
                pipeline_id = p.get("pipeline_id")
                if not pipeline_id or pipeline_id in cls._threads:
                    continue
                msg = "后端在运行中被中断（进程重启），该管线已标记为失败。"
                if PipelineManager.mark_failed(pipeline_id, msg):
                    logger.warning(f"[{pipeline_id}] 启动时回收孤儿管线 → failed")
                    data = PipelineManager.load(pipeline_id) or {}
                    tid = data.get("task_id")
                    if tid:
                        try:
                            task_manager.fail_task(tid, msg)
                        except Exception:
                            pass
        except Exception as e:  # noqa: BLE001 — 回收失败不应阻断启动
            logger.error(f"回收孤儿管线失败: {e}", exc_info=True)

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
        })
        PipelineManager.save(state)

        t = threading.Thread(
            target=cls._run,
            args=(state,),
            name=f"pipeline-{pipeline_id}",
            daemon=True,
        )
        cls._threads[pipeline_id] = t
        t.start()
        return state

    # -- 内部：进度辅助 ----------------------------------------------------

    @staticmethod
    def _global_from_stage(mode: str, stage: str, local_pct: int) -> int:
        bands = RESEARCH_ONLY_BANDS if mode == "research_only" else STAGE_BANDS
        lo, hi = bands.get(stage, (0, 100))
        local_pct = max(0, min(100, local_pct))
        return int(lo + (hi - lo) * local_pct / 100)

    def _make_stage_updater(self, state: PipelineState, stage: str):
        task_manager = TaskManager()

        def update(local_pct: int, message: str):
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
            state.global_progress = self._global_from_stage(state.mode, stage, local_pct)
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
        state.global_progress = self._global_from_stage(state.mode, stage, 100)
        PipelineManager.save(state)

    def _fail_stage(self, state: PipelineState, stage: str, error: str):
        st = state.stages.setdefault(stage, StageState(name=stage))
        st.status = "failed"
        st.error = error
        st.finished_at = _utcnow()
        PipelineManager.save(state)

    # -- 内部：主流程 ------------------------------------------------------

    @classmethod
    def _run(cls, state: PipelineState) -> None:
        self = cls()
        task_manager = TaskManager()
        try:
            # ---- Stage 0: RESEARCH ----
            upd = self._make_stage_updater(state, STAGE_RESEARCH)
            upd(1, "准备深度研究…")
            research = DeerFlowResearchRunner.run(
                state.prompt,
                state.handoff_dir or PipelineManager.handoff_dir(state.pipeline_id),
                on_progress=upd,
                depth=state.options.get("depth"),
            )
            self._complete_stage(state, STAGE_RESEARCH, "研究完成")
            report_md: str = research["report"]
            actors = research.get("actors")

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
            upd(10, "用研究报告创建项目…")
            project_name = state.options.get("project_name") or f"研究预测 {state.pipeline_id}"
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
            self._complete_stage(state, STAGE_ONTOLOGY, "本体生成完成")

            # ---- Stage 2: GRAPH ----
            upd = self._make_stage_updater(state, STAGE_GRAPH)
            upd(5, "构建知识图谱…")
            builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
            chunks = TextProcessor.split_text(report_md, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
            graph_id = builder.create_graph(name=project.name)
            builder.set_ontology(graph_id, project.ontology)

            def add_cb(msg: str, ratio: float):
                upd(int(10 + ratio * 55), msg)

            uuids = builder.add_text_batches(graph_id, chunks, batch_size=3, progress_callback=add_cb)

            def wait_cb(msg: str, ratio: float):
                upd(int(65 + ratio * 33), msg)

            builder._wait_for_episodes(uuids, wait_cb)
            project.graph_id = graph_id
            project.status = ProjectStatus.GRAPH_COMPLETED
            ProjectManager.save_project(project)
            state.graph_id = graph_id
            self._complete_stage(state, STAGE_GRAPH, "图谱构建完成")

            # ---- Stage 3: PREPARE ----
            upd = self._make_stage_updater(state, STAGE_PREPARE)
            upd(5, "创建模拟…")
            sim_manager = SimulationManager()
            sim_state = sim_manager.create_simulation(project.project_id, graph_id, enable_twitter=True, enable_reddit=True)
            state.simulation_id = sim_state.simulation_id
            PipelineManager.save(state)

            def prepare_cb(stage: str, progress: int, message: str, **_kwargs):
                upd(max(5, min(99, int(progress))), f"{stage}: {message}")

            sim_manager.prepare_simulation(
                simulation_id=sim_state.simulation_id,
                simulation_requirement=state.prompt,
                document_text=report_md,
                progress_callback=prepare_cb,
            )
            self._complete_stage(state, STAGE_PREPARE, "环境就绪")

            # ---- Stage 4: RUN ----
            upd = self._make_stage_updater(state, STAGE_RUN)
            upd(2, "启动 OASIS 模拟…")
            run_kwargs: dict[str, Any] = {"platform": "parallel"}
            _mr = state.options.get("max_rounds")
            if _mr:
                run_kwargs["max_rounds"] = int(_mr)
            SimulationRunner.start_simulation(simulation_id=sim_state.simulation_id, **run_kwargs)
            # 轮询直到完成
            while True:
                rs = SimulationRunner.get_run_state(sim_state.simulation_id)
                if rs is None:
                    raise RuntimeError("模拟运行状态丢失")
                total = getattr(rs, "total_rounds", 0) or 0
                cur = getattr(rs, "current_round", 0) or 0
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
            self._complete_stage(state, STAGE_RUN, "模拟完成")

            # ---- Stage 5: REPORT ----
            upd = self._make_stage_updater(state, STAGE_REPORT)
            upd(5, "生成预测报告…")
            report_id = f"report_{uuid.uuid4().hex[:12]}"
            agent = ReportAgent(
                graph_id=graph_id,
                simulation_id=sim_state.simulation_id,
                simulation_requirement=state.prompt,
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
            # 线程结束即从注册表移除，避免 _threads 无界增长，并让 reconcile_orphans 的
            # "pid in _threads" 判定准确反映当前在飞的线程。
            cls._threads.pop(state.pipeline_id, None)
