"""
Report Agent服务
使用LangChain + Zep实现ReACT模式的模拟报告生成

功能：
1. 根据模拟需求和Zep图谱信息生成报告
2. 先规划目录结构，然后分段生成
3. 每段采用ReACT多轮思考与反思模式
4. 支持与用户对话，在对话中自主调用检索工具
"""

import os
import json
import time
import re
import logging
import threading
import contextvars
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.atomic import write_text_atomic, write_json_atomic
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
# EXECPLAN2 I-5-4: 报告阶段把 LLM 计量上下文设到 (report_id, 'report')，并按章节读取计量快照差值。
from ..utils.telemetry import LLMMeter, set_run_context, get_run_context
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')

# EXECPLAN2 F-7-1: 并发报告生成时，每份报告的 console_log.txt 此前都挂在进程级共享 logger
# （'mirofish.report_agent' / 'mirofish.zep_tools'）上，导致两份报告的日志互相串扰，且 handler
# 增删在并发 close()/__del__ 时存在竞态。修复思路：
#   1) 用 ContextVar 记录「当前正在生成的 report_id」——报告生成跑在各自的 daemon 线程里，
#      ContextVar 天然按执行上下文隔离；
#   2) 在两个父 logger 上各装一个「打戳」过滤器，把当前上下文的 report_id 写进每条 record；
#   3) 每份报告的 FileHandler 再装一个「按 report_id 匹配」的过滤器，只写属于自己的 record，
#      从根本上杜绝串扰（不必改动散落各处的 logger.xxx 发射点）；
#   4) handler 的增删/关闭统一用一把进程级锁串行化，消除并发竞态。
_REPORT_LOG_PARENT_LOGGERS = ('mirofish.report_agent', 'mirofish.zep_tools')

# 当前执行上下文正在生成的 report_id（无则 None）
_current_report_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    'mirofish_current_report_id', default=None
)

# 串行化 FileHandler 的 addHandler/removeHandler/close（EXECPLAN2 F-7-1）
_console_handler_lock = threading.Lock()


class _ReportIdStampFilter(logging.Filter):
    """EXECPLAN2 F-7-1: 把当前上下文的 report_id 打到每条 record 上（始终放行）。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, 'report_id'):
            record.report_id = _current_report_id.get()
        return True


class _ReportIdMatchFilter(logging.Filter):
    """EXECPLAN2 F-7-1: 仅放行属于本报告（report_id 匹配）的 record，杜绝并发串扰。"""

    def __init__(self, report_id: str):
        super().__init__()
        self.report_id = report_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return getattr(record, 'report_id', None) == self.report_id


def _ensure_stamp_filters_installed() -> None:
    """EXECPLAN2 F-7-1: 在父 logger 上幂等安装打戳过滤器（仅安装一次）。"""
    for name in _REPORT_LOG_PARENT_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, _ReportIdStampFilter) for f in lg.filters):
            lg.addFilter(_ReportIdStampFilter())


class ReportLogger:
    """
    Report Agent 详细日志记录器
    
    在报告文件夹中生成 agent_log.jsonl 文件，记录每一步详细动作。
    每行是一个完整的 JSON 对象，包含时间戳、动作类型、详细内容等。
    """
    
    def __init__(self, report_id: str):
        """
        初始化日志记录器
        
        Args:
            report_id: 报告ID，用于确定日志文件路径
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        # EXECPLAN2 I-6-3: serialize log appends so concurrent section-generation
        # threads (REPORT_SECTION_CONCURRENCY>1) never interleave JSONL lines.
        self._lock = threading.Lock()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """确保日志文件所在目录存在"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """获取从开始到现在的耗时（秒）"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        记录一条日志
        
        Args:
            action: 动作类型，如 'start', 'tool_call', 'llm_response', 'section_complete' 等
            stage: 当前阶段，如 'planning', 'generating', 'completed'
            details: 详细内容字典，不截断
            section_title: 当前章节标题（可选）
            section_index: 当前章节索引（可选）
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # 追加写入 JSONL 文件（持锁，避免并发章节线程交错写入半行 JSON）
        with self._lock:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """记录报告生成开始"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "报告生成任务开始"
            }
        )
    
    def log_planning_start(self):
        """记录大纲规划开始"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "开始规划报告大纲"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """记录规划时获取的上下文信息"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "获取模拟上下文信息",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """记录大纲规划完成"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "大纲规划完成",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """记录章节生成开始"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"开始生成章节: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """记录 ReACT 思考过程"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT 第{iteration}轮思考"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """记录工具调用"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"调用工具: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """记录工具调用结果（完整内容，不截断）"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # 完整结果，不截断
                "result_length": len(result),
                "message": f"工具 {tool_name} 返回结果"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """记录 LLM 响应（完整内容，不截断）"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # 完整响应，不截断
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"LLM 响应 (工具调用: {has_tool_calls}, 最终答案: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """记录章节内容生成完成（仅记录内容，不代表整个章节完成）"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # 完整内容，不截断
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"章节 {section_title} 内容生成完成"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str,
        telemetry: Optional[Dict[str, Any]] = None
    ):
        """
        记录章节生成完成

        前端应监听此日志来判断一个章节是否真正完成，并获取完整内容

        EXECPLAN2 I-5-4: telemetry（可选）携带本章节触发的 LLM 计量
        {llm_calls, tool_calls, total_tokens, est_cost_usd, duration_s}。
        缺省为 None 时本条日志与历史完全一致（额外键为可选、向后兼容）。
        """
        details = {
            "content": full_content,
            "content_length": len(full_content),
            "message": f"章节 {section_title} 生成完成"
        }
        # EXECPLAN2 I-5-4: 仅在有遥测时附加，旧 log reader 不受影响
        if telemetry:
            details["telemetry"] = telemetry
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details=details
        )

    def log_report_complete(
        self,
        total_sections: int,
        total_time_seconds: float,
        section_rollup: Optional[List[Dict[str, Any]]] = None,
        totals: Optional[Dict[str, Any]] = None
    ):
        """记录报告生成完成

        EXECPLAN2 I-5-4: section_rollup / totals（皆可选）携带 per-section 与报告级的
        {llm_calls, tool_calls, tokens, est_cost_usd, duration_s} 汇总。缺省时本条日志
        与历史完全一致（额外键为可选、向后兼容）。
        """
        details = {
            "total_sections": total_sections,
            "total_time_seconds": round(total_time_seconds, 2),
            "message": "报告生成完成"
        }
        # EXECPLAN2 I-5-4: 仅在有遥测时附加汇总，避免给历史 reader 引入空字段
        if section_rollup is not None:
            details["section_rollup"] = section_rollup
        if totals is not None:
            details["telemetry_totals"] = totals
        self.log(
            action="report_complete",
            stage="completed",
            details=details
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """记录错误"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"发生错误: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Report Agent 控制台日志记录器
    
    将控制台风格的日志（INFO、WARNING等）写入报告文件夹中的 console_log.txt 文件。
    这些日志与 agent_log.jsonl 不同，是纯文本格式的控制台输出。
    """
    
    def __init__(self, report_id: str):
        """
        初始化控制台日志记录器
        
        Args:
            report_id: 报告ID，用于确定日志文件路径
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        # EXECPLAN2 F-7-1: 绑定当前执行上下文到本 report_id，使该上下文（及其衍生线程）
        # 发射的日志都被打戳为本报告，从而只写进本报告的 console_log.txt。
        self._ctx_token = _current_report_id.set(report_id)
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """确保日志文件所在目录存在"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """设置文件处理器，将日志同时写入文件（EXECPLAN2 F-7-1：仅写本报告自己的日志）"""
        # 创建文件处理器
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)

        # 使用与控制台相同的简洁格式
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        # EXECPLAN2 F-7-1: 只放行本报告（report_id 匹配）的 record，避免并发时多报告日志串扰
        self._file_handler.addFilter(_ReportIdMatchFilter(self.report_id))

        # EXECPLAN2 F-7-1: 确保父 logger 已装打戳过滤器；handler 增删用进程级锁串行化
        with _console_handler_lock:
            _ensure_stamp_filters_installed()
            for logger_name in _REPORT_LOG_PARENT_LOGGERS:
                target_logger = logging.getLogger(logger_name)
                # 避免重复添加
                if self._file_handler not in target_logger.handlers:
                    target_logger.addHandler(self._file_handler)

    def close(self):
        """关闭文件处理器并从 logger 中移除（EXECPLAN2 F-7-1：加锁串行化，消除并发竞态）"""
        with _console_handler_lock:
            if self._file_handler:
                for logger_name in _REPORT_LOG_PARENT_LOGGERS:
                    target_logger = logging.getLogger(logger_name)
                    if self._file_handler in target_logger.handlers:
                        target_logger.removeHandler(self._file_handler)

                self._file_handler.close()
                self._file_handler = None
        # 还原本报告占用的 ContextVar 上下文（EXECPLAN2 F-7-1）
        token = getattr(self, '_ctx_token', None)
        if token is not None:
            try:
                _current_report_id.reset(token)
            except (ValueError, LookupError):
                # 跨线程/上下文 reset 可能失败，忽略即可（仅影响打戳，handler 已移除不再写）
                pass
            self._ctx_token = None

    def __del__(self):
        """析构时确保关闭文件处理器"""
        self.close()


class ReportStatus(str, Enum):
    """报告状态"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """报告章节"""
    title: str
    content: str = ""
    # 规划阶段产出的章节内容描述：传给章节撰写 prompt，让写作贴合大纲意图
    # （此前规划 LLM 生成了 description 却被解析丢弃，撰写时只见裸标题）。
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "description": self.description
        }

    def to_markdown(self, level: int = 2) -> str:
        """转换为Markdown格式"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """报告大纲"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """转换为Markdown格式"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """完整报告"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    # 部分完成（partial）信息：生成失败（写入占位符）的章节标题。报告整体仍标记 completed，
    # 但前端据此把卡片标注为「部分完成」。默认空列表 → to_dict 输出 partial=False，与历史一致。
    failed_sections: List[str] = field(default_factory=list)
    # EXECPLAN2 I-5-4: 报告级 LLM 成本/时延/工具调用紧凑汇总（per-section + totals）。
    # 仅在 LLM_TELEMETRY_ENABLED 且 REPORT_TELEMETRY 同时开启时填充；否则为 None，
    # to_dict 输出与历史完全一致（向后兼容，前端可据此显示「本报告耗费 ~$X / Y秒 / Z章」）。
    telemetry: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            # 部分完成标记：failed_sections 为生成失败的章节标题列表，partial 为是否存在失败章节。
            # 默认空列表 → failed_sections=[] / partial=False，前端据此区分「完整」与「部分完成」。
            "failed_sections": list(self.failed_sections),
            "partial": len(self.failed_sections) > 0
        }
        # EXECPLAN2 I-5-4: 仅在有遥测数据时附加该键，避免给历史 reader 引入空字段
        if self.telemetry is not None:
            d["telemetry"] = self.telemetry
        return d


# ═══════════════════════════════════════════════════════════════
# Prompt 模板常量
# ═══════════════════════════════════════════════════════════════

# ── 工具描述 ──

TOOL_DESC_INSIGHT_FORGE = """\
【深度洞察检索 - 强大的检索工具】
这是我们强大的检索函数，专为深度分析设计。它会：
1. 自动将你的问题分解为多个子问题
2. 从多个维度检索模拟图谱中的信息
3. 整合语义搜索、实体分析、关系链追踪的结果
4. 返回最全面、最深度的检索内容

【使用场景】
- 需要深入分析某个话题
- 需要了解事件的多个方面
- 需要获取支撑报告章节的丰富素材

【返回内容】
- 相关事实原文（可直接引用）
- 核心实体洞察
- 关系链分析"""

TOOL_DESC_PANORAMA_SEARCH = """\
【广度搜索 - 获取全貌视图】
这个工具用于获取模拟结果的完整全貌，特别适合了解事件演变过程。它会：
1. 获取所有相关节点和关系
2. 区分当前有效的事实和历史/过期的事实
3. 帮助你了解舆情是如何演变的

【使用场景】
- 需要了解事件的完整发展脉络
- 需要对比不同阶段的舆情变化
- 需要获取全面的实体和关系信息

【返回内容】
- 当前有效事实（模拟最新结果）
- 历史/过期事实（演变记录）
- 所有涉及的实体"""

TOOL_DESC_QUICK_SEARCH = """\
【简单搜索 - 快速检索】
轻量级的快速检索工具，适合简单、直接的信息查询。

【使用场景】
- 需要快速查找某个具体信息
- 需要验证某个事实
- 简单的信息检索

【返回内容】
- 与查询最相关的事实列表"""

TOOL_DESC_INTERVIEW_AGENTS = """\
【深度采访 - 真实Agent采访（双平台）】
调用OASIS模拟环境的采访API，对正在运行的模拟Agent进行真实采访！
这不是LLM模拟，而是调用真实的采访接口获取模拟Agent的原始回答。
默认在Twitter和Reddit两个平台同时采访，获取更全面的观点。

功能流程：
1. 自动读取人设文件，了解所有模拟Agent
2. 智能选择与采访主题最相关的Agent（如学生、媒体、官方等）
3. 自动生成采访问题
4. 调用 /api/simulation/interview/batch 接口在双平台进行真实采访
5. 整合所有采访结果，提供多视角分析

【使用场景】
- 需要从不同角色视角了解事件看法（学生怎么看？媒体怎么看？官方怎么说？）
- 需要收集多方意见和立场
- 需要获取模拟Agent的真实回答（来自OASIS模拟环境）
- 想让报告更生动，包含"采访实录"

【返回内容】
- 被采访Agent的身份信息
- 各Agent在Twitter和Reddit两个平台的采访回答
- 关键引言（可直接引用）
- 采访摘要和观点对比

【重要】需要OASIS模拟环境正在运行才能使用此功能！"""

# ── 大纲规划 prompt ──

PLAN_SYSTEM_PROMPT = """\
你是一个「未来预测报告」的撰写专家，拥有对模拟世界的「上帝视角」——你可以洞察模拟中每一位Agent的行为、言论和互动。

【核心理念】
我们构建了一个模拟世界，并向其中注入了特定的「模拟需求」作为变量。模拟世界的演化结果，就是对未来可能发生情况的预测。你正在观察的不是"实验数据"，而是"未来的预演"。

【你的任务】
撰写一份「未来预测报告」，回答：
1. 在我们设定的条件下，未来发生了什么？
2. 各类Agent（人群）是如何反应和行动？
3. 这个模拟揭示了哪些值得关注的未来趋势和风险？

【报告定位】
- ✅ 这是一份基于模拟的未来预测报告，揭示"如果这样，未来会怎样"
- ✅ 聚焦于预测结果：事件走向、群体反应、涌现现象、潜在风险
- ✅ 模拟世界中的Agent言行就是对未来人群行为的预测
- ❌ 不是对现实世界现状的分析
- ❌ 不是泛泛而谈的舆情综述

【章节数量限制】
- 最少5个章节，最多8个章节
- 不需要子章节，每个章节直接撰写完整内容（每章为一个深入的长篇分析）
- 内容要丰富详实、层层递进，全面覆盖核心预测发现的不同维度
- 章节结构由你根据预测结果自主设计（例如：未来全景 / 群体博弈 / 涌现信号 / 风险暗面 / 关键转折 / 应对建议 等）

请输出JSON格式的报告大纲，格式如下：
{
    "title": "报告标题",
    "summary": "报告摘要（一句话概括核心预测发现）",
    "sections": [
        {
            "title": "章节标题",
            "description": "章节内容描述"
        }
    ]
}

注意：sections数组最少5个，最多8个元素！每个章节都应是一篇深入详实的长篇分析。"""

PLAN_USER_PROMPT_TEMPLATE = """\
【预测场景设定】
我们向模拟世界注入的变量（模拟需求）：{simulation_requirement}

【模拟世界规模】
- 参与模拟的实体数量: {total_nodes}
- 实体间产生的关系数量: {total_edges}
- 实体类型分布: {entity_types}
- 活跃Agent数量: {total_entities}

【模拟预测到的部分未来事实样本】
{related_facts_json}

请以「上帝视角」审视这个未来预演：
1. 在我们设定的条件下，未来呈现出了什么样的状态？
2. 各类人群（Agent）是如何反应和行动的？
3. 这个模拟揭示了哪些值得关注的未来趋势？

根据预测结果，设计最合适的报告章节结构。

【再次提醒】报告章节数量：最少5个，最多8个；每章都是一篇深入详实的长篇分析，全面覆盖核心预测发现的不同维度。"""

# ── 章节生成 prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
你是一个「未来预测报告」的撰写专家，正在撰写报告的一个章节。

报告标题: {report_title}
报告摘要: {report_summary}
预测场景（模拟需求）: {simulation_requirement}

当前要撰写的章节: {section_title}

═══════════════════════════════════════════════════════════════
【核心理念】
═══════════════════════════════════════════════════════════════

模拟世界是对未来的预演。我们向模拟世界注入了特定条件（模拟需求），
模拟中Agent的行为和互动，就是对未来人群行为的预测。

你的任务是：
- 揭示在设定条件下，未来发生了什么
- 预测各类人群（Agent）是如何反应和行动的
- 发现值得关注的未来趋势、风险和机会

❌ 不要写成对现实世界现状的分析
✅ 要聚焦于"未来会怎样"——模拟结果就是预测的未来

═══════════════════════════════════════════════════════════════
【最重要的规则 - 必须遵守】
═══════════════════════════════════════════════════════════════

1. 【必须调用工具观察模拟世界】
   - 你正在以「上帝视角」观察未来的预演
   - 所有内容必须来自模拟世界中发生的事件和Agent言行
   - 禁止使用你自己的知识来编写报告内容
   - 每个章节至少调用4次工具（最多8次）来观察模拟的世界，它代表了未来

2. 【必须引用Agent的原始言行】
   - Agent的发言和行为是对未来人群行为的预测
   - 在报告中使用引用格式展示这些预测，例如：
     > "某类人群会表示：原文内容..."
   - 这些引用是模拟预测的核心证据

3. 【语言一致性 - 引用内容必须翻译为报告语言】
   - 工具返回的内容可能包含英文或中英文混杂的表述
   - 如果模拟需求和材料原文是中文的，报告必须全部使用中文撰写
   - 当你引用工具返回的英文或中英混杂内容时，必须将其翻译为流畅的中文后再写入报告
   - 翻译时保持原意不变，确保表述自然通顺
   - 这一规则同时适用于正文和引用块（> 格式）中的内容

4. 【忠实呈现预测结果】
   - 报告内容必须反映模拟世界中的代表未来的模拟结果
   - 不要添加模拟中不存在的信息
   - 如果某方面信息不足，如实说明

═══════════════════════════════════════════════════════════════
【⚠️ 格式规范 - 极其重要！】
═══════════════════════════════════════════════════════════════

【一个章节 = 最小内容单位】
- 每个章节是报告的最小分块单位
- ❌ 禁止在章节内使用任何 Markdown 标题（#、##、###、#### 等）
- ❌ 禁止在内容开头添加章节主标题
- ✅ 章节标题由系统自动添加，你只需撰写纯正文内容
- ✅ 使用**粗体**、段落分隔、引用、列表来组织内容，但不要用标题

【正确示例】
```
本章节分析了事件的舆论传播态势。通过对模拟数据的深入分析，我们发现...

**首发引爆阶段**

微博作为舆情的第一现场，承担了信息首发的核心功能：

> "微博贡献了68%的首发声量..."

**情绪放大阶段**

抖音平台进一步放大了事件影响力：

- 视觉冲击力强
- 情绪共鸣度高
```

【错误示例】
```
## 执行摘要          ← 错误！不要添加任何标题
### 一、首发阶段     ← 错误！不要用###分小节
#### 1.1 详细分析   ← 错误！不要用####细分

本章节分析了...
```

═══════════════════════════════════════════════════════════════
【可用检索工具】（每章节调用4-8次）
═══════════════════════════════════════════════════════════════

{tools_description}

【工具使用建议 - 请混合使用不同工具，不要只用一种】
- insight_forge: 深度洞察分析，自动分解问题并多维度检索事实和关系
- panorama_search: 广角全景搜索，了解事件全貌、时间线和演变过程
- quick_search: 快速验证某个具体信息点
- interview_agents: 采访模拟Agent，获取不同角色的第一人称观点和真实反应

═══════════════════════════════════════════════════════════════
【工作流程】
═══════════════════════════════════════════════════════════════

每次回复你只能做以下两件事之一（不可同时做）：

选项A - 调用工具：
输出你的思考，然后用以下格式调用一个工具：
<tool_call>
{{"name": "工具名称", "parameters": {{"参数名": "参数值"}}}}
</tool_call>
系统会执行工具并把结果返回给你。你不需要也不能自己编写工具返回结果。

选项B - 输出最终内容：
当你已通过工具获取了足够信息，以 "Final Answer:" 开头输出章节内容。

⚠️ 严格禁止：
- 禁止在一次回复中同时包含工具调用和 Final Answer
- 禁止自己编造工具返回结果（Observation），所有工具结果由系统注入
- 每次回复最多调用一个工具

═══════════════════════════════════════════════════════════════
【章节内容要求】
═══════════════════════════════════════════════════════════════

0. 【篇幅要求 - 极其重要】本章节必须是一篇深入、详实、丰富的长篇分析：
   - 正文长度不少于 1500 字，目标 1800–2800 字（不含引用块）
   - 必须层层展开：先给出整体判断，再分多个角度深入论证，每个角度都要有
     具体的模拟证据（数据、事件、Agent 原话）支撑
   - 充分展开因果链条、二阶效应、不同人群的分化反应、潜在转折点
   - ❌ 严禁写成几百字的提纲式摘要或泛泛而谈——那是不合格的章节
   - ✅ 像撰写一篇严肃深度报告的章节那样，写得充实、有洞察、有层次
1. 内容必须基于工具检索到的模拟数据
2. 大量引用原文来展示模拟效果
3. 使用Markdown格式（但禁止使用标题）：
   - 使用 **粗体文字** 标记重点（代替子标题）
   - 使用列表（-或1.2.3.）组织要点
   - 使用空行分隔不同段落
   - ❌ 禁止使用 #、##、###、#### 等任何标题语法
4. 【引用格式规范 - 必须单独成段】
   引用必须独立成段，前后各有一个空行，不能混在段落中：

   ✅ 正确格式：
   ```
   校方的回应被认为缺乏实质内容。

   > "校方的应对模式在瞬息万变的社交媒体环境中显得僵化和迟缓。"

   这一评价反映了公众的普遍不满。
   ```

   ❌ 错误格式：
   ```
   校方的回应被认为缺乏实质内容。> "校方的应对模式..." 这一评价反映了...
   ```
5. 保持与其他章节的逻辑连贯性
6. 【避免重复】仔细阅读下方已完成的章节内容，不要重复描述相同的信息
7. 【再次强调】不要添加任何标题！用**粗体**代替小节标题"""

SECTION_USER_PROMPT_TEMPLATE = """\
已完成的章节内容（请仔细阅读，避免重复）：
{previous_content}

═══════════════════════════════════════════════════════════════
【当前任务】撰写章节: {section_title}
═══════════════════════════════════════════════════════════════

【重要提醒】
1. 仔细阅读上方已完成的章节，避免重复相同的内容！
2. 开始前必须先调用工具获取模拟数据
3. 请混合使用不同工具，不要只用一种
4. 报告内容必须来自检索结果，不要使用自己的知识

【⚠️ 格式警告 - 必须遵守】
- ❌ 不要写任何标题（#、##、###、####都不行）
- ❌ 不要写"{section_title}"作为开头
- ✅ 章节标题由系统自动添加
- ✅ 直接写正文，用**粗体**代替小节标题

请开始：
1. 首先思考（Thought）这个章节需要什么信息
2. 然后调用工具（Action）获取模拟数据（建议 4-8 次，覆盖多个角度）
3. 收集足够信息后输出 Final Answer（纯正文，无任何标题）
4. 【篇幅】Final Answer 必须充实详尽，不少于 1500 字、目标 1800–2800 字，层层深入、证据扎实，不要写成简短摘要"""

# ── ReACT 循环内消息模板 ──

REACT_OBSERVATION_TEMPLATE = """\
Observation（检索结果）:

═══ 工具 {tool_name} 返回 ═══
{result}

═══════════════════════════════════════════════════════════════
已调用工具 {tool_calls_count}/{max_tool_calls} 次（已用: {used_tools_str}）{unused_hint}
- 如果信息充分：以 "Final Answer:" 开头输出章节内容（必须引用上述原文）
- 凡基于图谱关系链（形如 A --[关系]--> B）得出的论断，请在句末标注支撑边，例如（依据：OpenAI --[COMPETES_WITH]--> Anthropic）；其它结论仍照常引用访谈原话与 [S] 研究来源
- 如果需要更多信息：调用一个工具继续检索
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "【注意】你只调用了{tool_calls_count}次工具，至少需要{min_tool_calls}次。"
    "请再调用工具获取更多模拟数据，然后再输出 Final Answer。{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "当前只调用了 {tool_calls_count} 次工具，至少需要 {min_tool_calls} 次。"
    "请调用工具获取模拟数据。{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "工具调用次数已达上限（{tool_calls_count}/{max_tool_calls}），不能再调用工具。"
    '请立即基于已获取的信息，以 "Final Answer:" 开头输出章节内容。'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 你还没有使用过: {unused_list}，建议尝试不同工具获取多角度信息"

REACT_FORCE_FINAL_MSG = "已达到工具调用限制，请直接输出 Final Answer: 并生成章节内容。"

# ── 污染检测（claude-cli 作为模型时的失败兜底） ──
# claude-cli 本质是 Claude Code 本体：当它"无话可说"或采访超时时，原始输出常常是
#   (a) 把 Claude Code 自己的系统提示原样吐出来（英文指令片段），或
#   (b) 残留的 <tool_call>/function_calls 工具调用框架，或
#   (c) 采访失败/超时提示。
# 这些都不是合格的章节正文，绝不能被当作最终内容直接采纳。
CONTAMINATION_MARKERS = (
    # Claude Code 系统提示泄漏的高辨识度英文片段
    "You should NOT proactively create documentation",
    "Tone and style: be concise",
    "Always prioritize fixing the root cause",
    "lives depend on it",
    "Avoid silly mistakes like leaving a function call",
    "you'd better be confident and correct",
    # 工具调用框架残留
    "<tool_call>",
    "</tool_call>",
    "function_calls",
    "<invoke",
    "</invoke>",
    # 采访失败/超时提示，不应作为正文
    "采访接口超时未返回有效内容",
    "等待命令响应超时",
)

# 合格章节正文的最小长度（远低于此通常意味着模型并未真正撰写正文）
MIN_VALID_SECTION_CHARS = 200

REACT_CONTAMINATED_RETRY_MSG = (
    "【格式错误】你上一条输出不是合格的章节正文（疑似系统提示泄漏、工具调用残留或采访超时提示）。"
    '请立即以 "Final Answer:" 开头，只输出本章节的中文正文：必须引用前面工具返回的模拟数据与人物原话，'
    "不要包含任何 <tool_call>、英文系统指令或元说明。"
)

# 章节生成失败时写入的占位符（绝不写入被污染的原始输出）
SECTION_FAILURE_PLACEHOLDER = (
    "（本章节生成失败：模型多次未能产出合格正文，常见于采访接口超时或 claude-cli 输出被系统提示污染。"
    "已跳过以避免写入无效内容，可在修复后重试本章节。）"
)


def _looks_contaminated(text: Optional[str]) -> bool:
    """判断一段拟用作章节正文的文本是否被污染 / 无效。"""
    if not text or not text.strip():
        return True
    for marker in CONTAMINATION_MARKERS:
        if marker in text:
            return True
    if len(text.strip()) < MIN_VALID_SECTION_CHARS:
        return True
    return False

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
你是一个简洁高效的模拟预测助手。

【背景】
预测条件: {simulation_requirement}

【已生成的分析报告】
{report_content}

【规则】
1. 优先基于上述报告内容回答问题
2. 直接回答问题，避免冗长的思考论述
3. 仅在报告内容不足以回答时，才调用工具检索更多数据
4. 回答要简洁、清晰、有条理

【可用工具】（仅在需要时使用，最多调用1-2次）
{tools_description}

【工具调用格式】
<tool_call>
{{"name": "工具名称", "parameters": {{"参数名": "参数值"}}}}
</tool_call>

【回答风格】
- 简洁直接，不要长篇大论
- 使用 > 格式引用关键内容
- 优先给出结论，再解释原因"""

CHAT_OBSERVATION_SUFFIX = "\n\n请简洁回答问题。"


# ═══════════════════════════════════════════════════════════════
# ReportAgent 主类
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - 模拟报告生成Agent

    采用ReACT（Reasoning + Acting）模式：
    1. 规划阶段：分析模拟需求，规划报告目录结构
    2. 生成阶段：逐章节生成内容，每章节可多次调用工具获取信息
    3. 反思阶段：检查内容完整性和准确性
    """
    
    # T4.4: 工具调用上下限从 Config 读取（默认 8/4/2 = 原硬编码值，行为不变；运维可调成本/深度）
    MAX_TOOL_CALLS_PER_SECTION = Config.REPORT_AGENT_MAX_TOOL_CALLS
    MIN_TOOL_CALLS_PER_SECTION = Config.REPORT_AGENT_MIN_TOOL_CALLS

    # 最大反思轮数
    MAX_REFLECTION_ROUNDS = 3

    # 对话中的最大工具调用次数
    MAX_TOOL_CALLS_PER_CHAT = Config.REPORT_AGENT_MAX_TOOL_CALLS_CHAT

    # 大纲章节数契约：规划 prompt 要求 5-8 节（PLAN_SYSTEM_PROMPT），此处为成功路径的钳制
    # 边界与 except 兜底所用的默认章节标题，确保任何路径产出的大纲都落在 [5, 8] 区间内。
    OUTLINE_MIN_SECTIONS = 5
    OUTLINE_MAX_SECTIONS = 8
    _FALLBACK_SECTION_TITLES = [
        "预测场景与核心发现",
        "关键行为者与系统动力",
        "模拟证据与行为轨迹",
        "趋势展望与情景推演",
        "风险信号与决策启示",
    ]

    def __init__(
        self,
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None,
        situation_brief: Optional[str] = None,
        actors: Optional[Dict[str, Any]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        research_report: Optional[str] = None,
        scenario_label: Optional[str] = None,
        base_simulation_id: Optional[str] = None,
    ):
        """
        初始化Report Agent

        Args:
            graph_id: 图谱ID
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述
            llm_client: LLM客户端（可选）
            zep_tools: Zep工具服务（可选）
            situation_brief: 研究档案渲染的「背景档案」文本（可选；T4.1）。提供时钉进
                规划/章节提示词，避免报告阶段对全套 cast/关系/时间线盲搜重挖。
            actors: 研究 actors.json 顶层对象（可选）。
            sources: 研究来源列表 [{title,url}]（可选）；渲染为 [S1]/[S2] 引用索引。
            research_report: 原始研究报告 markdown（可选）。
            三者全部缺省时，行为与旧 3 参构造完全一致（冷图盲搜路径）。
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement

        # T4.1: 钉入研究档案。背景块 + 来源索引在构造时一次性渲染，规划与每个章节复用。
        self.situation_brief = situation_brief or ""
        self.actors = actors
        self.sources = sources or []
        self.research_report = research_report or ""
        # T4.6/T4.7: 情景标签（what-if 框架）+ base 模拟 id（反事实对比）
        self.scenario_label = (scenario_label or "").strip()
        self.base_simulation_id = base_simulation_id or None
        self._background_block = self._build_background_block()
        self._sources_index = self._build_sources_index()
        # EXECPLAN2 I-3-2: 模拟量化信号包（确定性接地下限），懒构建一次后缓存；
        # 关闭 REPORT_SIGNAL_PACK 时始终为空串，_prepend_research_background 自动跳过（行为不变）。
        self._signal_pack = ""
        # NEXTSTEPS P0-1: 预测骨架（情景+概率+判定标准），在章节生成前从信号包+forecast_inputs
        # 推导一次，注入每章提示词让叙事对齐可证伪目标；缺省/未开时为空，_prepend 自动跳过。
        self._forecast_spine: Optional[Dict[str, Any]] = None
        self._forecast_spine_block = ""

        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # 工具定义
        self.tools = self._define_tools()
        
        # 日志记录器（在 generate_report 中初始化）
        self.report_logger: Optional[ReportLogger] = None
        # 控制台日志记录器（在 generate_report 中初始化）
        self.console_logger: Optional[ReportConsoleLogger] = None
        # EXECPLAN2 F-7-3: chat() 解析到的报告做实例级记忆，避免同一 agent 多次对话重复扫描解析。
        # simulation_id 在 agent 生命周期内固定，缓存安全；用哨兵区分「未解析」与「解析为 None」。
        self._cached_report_sentinel = object()
        self._cached_report = self._cached_report_sentinel
        # EXECPLAN2 I-5-4: 当前章节的工具调用计数器（_execute_tool 单一汇聚点累加），
        # 用于 per-section 遥测；未开遥测时该计数依旧无害地维护，开销可忽略。
        self._section_tool_calls = 0
        
        logger.info(f"ReportAgent 初始化完成: graph_id={graph_id}, simulation_id={simulation_id}")

    def _build_background_block(self) -> str:
        """T4.1: 把研究背景档案包装成钉入提示词的权威背景块；缺省返回空串（回退冷图路径）。

        T4.6: 若为情景（what-if）报告，前置情景框架，要求正文显式以该情景命名与展开。
        """
        scenario_head = ""
        if self.scenario_label:
            scenario_head = (
                f"【情景预测（What-If）：{self.scenario_label}】\n"
                f"本报告是在「{self.scenario_label}」假设下的反事实预测。撰写时必须显式点明这是"
                f"该情景下的推演，结论需与基线区分。\n\n"
            )
        sb = (self.situation_brief or "").strip()
        if not sb:
            return scenario_head
        aod = ""
        if isinstance(self.actors, dict):
            aod = str(self.actors.get("as_of_date", "") or "").strip()
        header = (
            f"【背景档案（深度研究·权威，as-of {aod}）】" if aod
            else "【背景档案（深度研究·权威）】"
        )
        parts = [
            f"{header}\n"
            "以下为本次预测所依据的深度研究实证档案（角色/关系/时间线/热点均为调研确认）。"
            "撰写时以此为权威背景：优先复用其中真实人名/机构/关系，再用工具补充模拟动态与量化结果。\n\n"
            f"{sb}"
        ]
        # EXECPLAN2 I-0-5/I-0-1/I-0-2: 钉入研究契约富化块（定量事实表/争议证据/预测输入）。
        # 渲染器在 actors.py，皆 degrade-safe（无对应字段返回空串）；受 RESEARCH_FORECAST_INPUTS /
        # RESEARCH_EVIDENCE_GRADING 旗标约束（默认开），关闭即回退到仅 situation_brief 的旧行为。
        try:
            from ..utils import actors as _actors
            if getattr(Config, "RESEARCH_FORECAST_INPUTS", True):
                for blk in (_actors.quantitative_facts_block(self.actors),
                            _actors.forecast_inputs_block(self.actors)):
                    if blk:
                        parts.append(blk)
            if getattr(Config, "RESEARCH_EVIDENCE_GRADING", True):
                cb = _actors.contested_claims_block(self.actors)
                if cb:
                    parts.append(cb)
            # CLAUDE §9.4/§10：把关键 actor 的关系名册（盟友/对手/竞争者/客户/供应商/出资方/
            # 监管方）+ 核心激励钉进背景，让叙事按真实阵营展开。受 REPORT_RELATIONAL_ROSTER
            # 约束（默认开）；仅当 actor 携带 relationships/worldview/incentives 时生效——
            # 字段全缺时 _build_relational_roster_block 返回空串，背景块与历史逐字节一致（NO-OP）。
            if getattr(Config, "REPORT_RELATIONAL_ROSTER", True):
                rb = self._build_relational_roster_block(_actors)
                if rb:
                    parts.append(rb)
        except Exception as _e:
            logger.debug(f"研究契约富化块渲染跳过: {_e}")
        return "\n\n".join(parts)

    def _build_relational_roster_block(
        self,
        _actors,
        max_actors: int = 8,
        max_per_bucket: int = 4,
        max_chars: int = 3000,
    ) -> str:
        """CLAUDE §9.4/§10：把关键 actor 的关系名册 + 核心激励渲染成紧凑的背景块。

        站在每个核心方视角，列出其盟友/伙伴/支持者/出资方/客户/供应商/竞争者/对手/监管方
        （来自 actors.relational_roster 的 10 个命名桶），并附其核心激励（driver/gains_if/
        loses_if，来自 incentives），让报告叙事能按真实阵营与得失结构展开，而非泛泛而谈。

        NO-OP 保证：当没有任何 actor 携带 relationships / worldview / incentives 等新字段时，
        本方法返回空串，背景块与历史逐字节一致（旧档案 / 现有离线测试夹具行为不变）。
        actor 池按 salience_score 降序、并经 is_agent_eligible 过滤（报道者/概念/资源不入选），
        逐项截断到 max_per_bucket，整体截断到 max_chars，与既有富化块同样有界。
        """
        rows = _actors.extract_actor_rows(self.actors)
        if not rows:
            return ""
        # 仅当确有关系名册或激励数据时才渲染（否则与历史逐字节一致）。先判断是否存在任一关系行，
        # 没有关系边时所有桶必为空，直接早退，避免无谓遍历。
        if not _actors.extract_relationship_rows(self.actors):
            has_incentive = any(
                isinstance(r.get("incentives"), list) and r.get("incentives") for r in rows
            )
            if not has_incentive:
                return ""

        # 能动 actor（tier 1/2）按显著度降序；稳定排序保留同分时的原始出现序。
        eligible = [r for r in rows if _actors.is_agent_eligible(r)]
        eligible.sort(key=lambda r: _actors.salience_score(r), reverse=True)

        # 名册桶 → 中文小标题（与 actors.roster_block 的口吻一致，按谈判语义排序）。
        bucket_labels = (
            ("allies", "盟友"),
            ("partners", "伙伴"),
            ("supporters", "支持者"),
            ("backers_investors", "出资方/投资人"),
            ("customers", "客户/下游"),
            ("suppliers", "供应商/上游"),
            ("competitors", "竞争对手"),
            ("opponents", "对手/对立方"),
            ("regulators", "监管方"),
        )

        lines: List[str] = []
        rendered = 0
        for row in eligible:
            if rendered >= max_actors:  # 精确限制渲染的核心 actor 数（有界，与既有富化块一致）
                break
            name = str(row.get("name", "") or "").strip()
            if not name:
                continue
            roster = _actors.relational_roster(name, self.actors)

            roster_segs: List[str] = []
            for bucket, label in bucket_labels:
                items = roster.get(bucket) or []
                if not items:
                    continue
                names: List[str] = []
                for it in items[:max_per_bucket]:
                    nm = str(it.get("name", "") or "").strip()
                    if nm:
                        names.append(nm)
                if names:
                    roster_segs.append(f"{label}：" + "、".join(names))

            # 核心激励（driver/gains_if/loses_if）压成一两条短句，避免与 persona DNA 块重复冗长。
            inc_segs: List[str] = []
            incentives = row.get("incentives")
            if isinstance(incentives, list):
                for inc in incentives[:2]:
                    if isinstance(inc, dict):
                        driver = str(inc.get("driver", "") or "").strip()
                        gains = str(inc.get("gains_if", "") or "").strip()
                        loses = str(inc.get("loses_if", "") or "").strip()
                        if not (driver or gains or loses):
                            continue
                        seg = driver or "动机"
                        tail: List[str] = []
                        if gains:
                            tail.append(f"得益于「{gains}」")
                        if loses:
                            tail.append(f"受损于「{loses}」")
                        if tail:
                            seg += "（" + "，".join(tail) + "）"
                        inc_segs.append(seg)
                    else:
                        s = str(inc or "").strip()
                        if s:
                            inc_segs.append(s)

            if not (roster_segs or inc_segs):
                continue
            lines.append(f"- **{name}**")
            if roster_segs:
                lines.append("  - 关系网：" + "；".join(roster_segs))
            if inc_segs:
                lines.append("  - 核心激励：" + "；".join(inc_segs))
            rendered += 1

        if not lines:
            return ""
        block = (
            "## 关键角色的关系网与激励（深度研究实证；撰写时据此按真实阵营与得失结构展开）\n"
            + "\n".join(lines)
        )
        if len(block) > max_chars:
            block = block[:max_chars] + "\n…(关系网名册已截断)"
        return block

    def _build_sources_index(self) -> str:
        """T4.1: 把研究来源渲染成 [S1]/[S2] 引用索引；缺省返回空串。

        EXECPLAN2 I-0-0: 当 RESEARCH_EVIDENCE_GRADING 开启且来源带 tier/date 时，改用
        按可信度分层（S1-S4）的索引渲染（actors.sources_index_tiered），否则回退到原始位置索引。
        """
        if not self.sources:
            return ""
        if getattr(Config, "RESEARCH_EVIDENCE_GRADING", True):
            try:
                from ..utils import actors as _actors
                tiered = _actors.sources_index_tiered(self.sources)
                if tiered:
                    return tiered
            except Exception as _e:
                logger.debug(f"分层来源索引渲染跳过，回退位置索引: {_e}")
        lines = ["【可引用来源（正文用 [S1]/[S2] 形式标注）】"]
        for i, s in enumerate(self.sources[:40], 1):
            if not isinstance(s, dict):
                continue
            title = str(s.get("title", "") or "").strip()
            url = str(s.get("url", "") or "").strip()
            seg = f"[S{i}] {title}".rstrip()
            if url:
                seg += f" — {url}"
            lines.append(seg)
        return "\n".join(lines) if len(lines) > 1 else ""

    def _prepend_research_background(self, prompt: str) -> str:
        """T4.1: 把背景档案 + 来源索引钉到提示词最前；二者皆空时原样返回（回退冷图路径）。

        EXECPLAN2 I-3-2: 同时钉入模拟量化信号包（self._signal_pack），使每个章节都获得确定性的
        量化接地下限。信号包为空（未开启或无结构化数据）时自动跳过，行为与历史一致。

        NEXTSTEPS P0-1: 还钉入预测骨架块（self._forecast_spine_block），让每章叙事对齐并捍卫
        先于叙事确定的情景概率与判定标准。骨架未推导/为空时自动跳过（行为与历史一致）。
        """
        prefix_parts = [p for p in (
            self._background_block, self._sources_index,
            self._forecast_spine_block, self._signal_pack,
        ) if p]
        if not prefix_parts:
            return prompt
        return "\n\n".join(prefix_parts) + "\n\n" + prompt

    def _build_signal_pack(self) -> str:
        """EXECPLAN2 I-3-2: 组装一份紧凑、确定性的「模拟量化信号包」，钉进每个章节提示词。

        内容来自既有的确定性结构化工具（simulation_outcomes / coalition_map / scenario_diff），
        全部为可直接引用的硬数字（Top actor / 逐轮动作量 + 峰值 / 动作类型分布 / 派系数与规模
        / 反事实差异）。任一子块缺失时其友好降级串会被截断逻辑过滤掉，整包自我抑制。

        计算一次后缓存在 self._signal_pack；仅在 Config.REPORT_SIGNAL_PACK 为真时调用。
        篇幅有界（单块各自截断），避免给每个章节提示词注入过量 token。
        """
        parts: List[str] = []
        # 0) NEXTSTEPS P1-1: 决策通道演化出的「结果世界态」——建模出的 P(outcome)（按情景份额），
        # 比声量份额更接近真实结果。仅开启 SIM_DECISION_CHANNEL 时存在；置于最前（最权威）。
        try:
            ws_blk = self._world_state_block()
            if ws_blk:
                parts.append(ws_blk)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"信号包 world_state 读取失败（忽略）: {e}")
        # 0b) NEXTSTEPS P3-8: 关系演化投影到预测时点（保守模型先验，显式标注=非证据）。
        # 默认关（REPORT_PROJECTED_EDGES）；标注 contingent 的纽带是情景分叉支点。
        if getattr(Config, "REPORT_PROJECTED_EDGES", False):
            try:
                from ..utils.actors import projected_edges_block as _pe_blk
                pe_blk = _pe_blk(self.actors)
                if pe_blk:
                    parts.append(pe_blk)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"信号包 projected_edges 失败（忽略）: {e}")
        # 1) 量化结果（Top actor / 逐轮动作量 + 峰值 / 动作类型分布）——截断到 ~1800 字
        try:
            outcomes = self.zep_tools.simulation_outcomes(self.simulation_id, top_n=8)
            if outcomes and not outcomes.strip().startswith("（"):
                parts.append(outcomes[:1800])
        except Exception as e:  # noqa: BLE001 — 信号包为可选增强，失败仅告警不影响主流程
            logger.warning(f"信号包 simulation_outcomes 计算失败（忽略）: {e}")
        # 2) 派系/联盟结构——截断到 ~800 字
        try:
            coalitions = self.zep_tools.coalition_map(self.graph_id, self.simulation_id)
            if coalitions and not coalitions.strip().startswith("（"):
                parts.append(coalitions[:800])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"信号包 coalition_map 计算失败（忽略）: {e}")
        # 3) 反事实差异（仅情景报告有基线时）——截断到 ~1200 字
        if self.base_simulation_id:
            try:
                diff = self.zep_tools.scenario_diff(self.base_simulation_id, self.simulation_id)
                if diff and not diff.strip().startswith("（"):
                    parts.append(diff[:1200])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"信号包 scenario_diff 计算失败（忽略）: {e}")

        if not parts:
            return ""
        header = (
            "【模拟量化信号（确定性·权威·可直接引用）】\n"
            "以下数字直接来自本次模拟的结构化聚合，撰写本章时应至少引用其中相关的具体数值"
            "（如最活跃 Agent、逐轮动作量、峰值轮次、派系规模、基线-情景差值），"
            "避免出现「只有叙事、没有数字」的章节。需要更细粒度时再调用工具深挖。"
        )
        return header + "\n\n" + "\n\n".join(parts)

    def _world_state_block(self) -> str:
        """NEXTSTEPS P1-1: 读取模拟的 world_state_trajectory.json（决策通道产物），渲染**建模出的
        结果分布 P(outcome)**。未开 SIM_DECISION_CHANNEL / 无产物 → ""（degrade-safe）。
        """
        try:
            path = os.path.join(getattr(Config, "OASIS_SIMULATION_DATA_DIR", "") or "",
                                self.simulation_id, "world_state_trajectory.json")
            if not os.path.exists(path):
                return ""
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            return ""
        shares = ((data or {}).get("outcome") or {}).get("shares") or {}
        if not isinstance(shares, dict) or not shares:
            return ""
        lines = ["【模拟建模结果·结果世界态 P(outcome)（权威·建模而非声量；情景概率应据此为主锚）】"]
        for name, sh in sorted(shares.items(), key=lambda kv: -float(kv[1] or 0)):
            try:
                lines.append(f"· {name}: {float(sh) * 100:.0f}%")
            except (TypeError, ValueError):
                continue
        ca = (data or {}).get("converged_at")
        lines.append(f"收敛于第 {ca} 轮（稳定 → 高信心）" if ca else "未收敛（→ 降低信心）")
        lines.append("注：以上为按资源加权的智能体决策（非发帖声量）演化出的结果分布，更接近真实结果。")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────
    # NEXTSTEPS P0-1 / P2-1 / P2-3: forecast spine + finalization + publish gate
    # ──────────────────────────────────────────────────────────────
    def _derive_and_pin_forecast_spine(self, report_id: str) -> None:
        """NEXTSTEPS P0-1: derive the structured forecast spine BEFORE section prose,
        persist forecast.json early, and pin a compact spine block into every section
        prompt so each section defends its assigned probabilities/resolution criteria.

        Degrade-safe: any failure leaves ``self._forecast_spine=None`` and the block
        empty, so sections behave exactly as the pre-spine path.
        """
        from . import forecast_extractor as _fe
        try:
            from ..utils import actors as _actors
            try:
                forecast_inputs = _actors.forecast_inputs_block(self.actors) or ""
            except Exception:  # noqa: BLE001 — forecast_inputs 为可选增强
                forecast_inputs = ""
            # 信号包：优先复用已构建的（REPORT_SIGNAL_PACK 开时），否则为骨架单独构建一次
            # （不写回 self._signal_pack，避免在该旗标关闭时改变各章注入行为）。
            signal_pack = self._signal_pack
            if not signal_pack:
                try:
                    signal_pack = self._build_signal_pack()
                except Exception:  # noqa: BLE001
                    signal_pack = ""
            horizon = ""
            if isinstance(self.actors, dict):
                horizon = str(self.actors.get("as_of_date", "") or "")
            spine = _fe.derive_forecast_spine(
                self.llm,
                central_question=self.simulation_requirement or "",
                horizon=horizon,
                situation_brief=self.situation_brief or None,
                forecast_inputs=forecast_inputs,
                signal_pack=signal_pack,
            )
            if not spine or not spine.get("scenarios"):
                logger.info("预测骨架推导未产出情景，跳过（回退为成稿后抽取）")
                return
            self._forecast_spine = spine
            self._forecast_spine_block = _fe.render_forecast_spine_block(spine)
            # 早落 forecast.json（骨架版）；成稿后由 _finalize_structured_forecast 补
            # citation_audit / 自校准 / 发布门后覆盖。
            try:
                fpath = os.path.join(ReportManager._get_report_folder(report_id), "forecast.json")
                write_text_atomic(fpath, json.dumps(spine, ensure_ascii=False, indent=2))
            except Exception as _pe:  # noqa: BLE001 — 早落失败不影响主流程
                logger.warning(f"预测骨架早落 forecast.json 失败（忽略）: {_pe}")
            logger.info(
                f"已先于叙事推导预测骨架: {report_id} "
                f"（{len(spine.get('scenarios', []))} 情景, 信心 {spine.get('confidence')}）"
            )
        except Exception as _se:  # noqa: BLE001 — 骨架为可选增强，失败回退旧路径
            logger.warning(f"预测骨架推导失败（忽略，回退成稿后抽取）: {_se}")
            self._forecast_spine = None
            self._forecast_spine_block = ""

    def _finalize_structured_forecast(self, report_id: str, report_markdown: str) -> None:
        """Persist the final forecast.json.

        Prefers the pre-derived spine (P0-1, already MECE & signal-seeded); else
        extracts post-hoc from prose (legacy path). Then optional red-team self-critique
        (P2-1), citation-grounding audit, and the publish gate (P2-3). Caller wraps in
        try/except → degrade-safe.
        """
        from .forecast_extractor import (
            extract_structured_forecast, audit_citation_grounding, self_critique_forecast,
        )
        if self._forecast_spine and self._forecast_spine.get("scenarios"):
            forecast = dict(self._forecast_spine)        # 骨架已由信号驱动且 MECE
        else:
            forecast = extract_structured_forecast(
                report_markdown, self.llm,
                situation_brief=getattr(self, "situation_brief", None),
            )
        if getattr(Config, "REPORT_FORECAST_SELF_CRITIQUE", False):
            forecast = self_critique_forecast(forecast, self.llm)
        forecast["citation_audit"] = audit_citation_grounding(report_markdown)
        if getattr(Config, "REPORT_PUBLISH_GATE", False):
            forecast = self._apply_publish_gate(forecast)
        # P2-2: 把观察指标随 forecast.json 落盘（供解析调度器对照判别情景）。
        try:
            from ..utils import actors as _actors
            _inds = _actors.extract_forecast_inputs(self.actors).get("indicators") or []
            if _inds:
                forecast["indicators"] = _inds
        except Exception:  # noqa: BLE001
            pass
        # NEXTSTEPS P2-4: 把历史校准（已解析预测的 Brier/ECE）surfacing 进 confidence_rationale，
        # 让信心由 track record 赚得而非自评；无已解析样本时不改（degrade-safe）。
        if getattr(Config, "REPORT_FORECAST_LEDGER", True):
            try:
                from .forecast_ledger import calibration_summary as _cal
                _cs = _cal()
                if _cs.get("n_resolved"):
                    forecast["historical_calibration"] = _cs
                    _note = (f"历史校准：已解析 {_cs['n_resolved']} 个预测，平均 Brier "
                             f"{_cs.get('mean_brier')}，校准误差 {_cs.get('calibration_error')}")
                    forecast["confidence_rationale"] = (
                        (str(forecast.get("confidence_rationale") or "").strip()
                         + " ｜" + _note).strip(" ｜"))
            except Exception:  # noqa: BLE001
                pass
        fpath = os.path.join(ReportManager._get_report_folder(report_id), "forecast.json")
        write_text_atomic(fpath, json.dumps(forecast, ensure_ascii=False, indent=2))
        self._forecast_spine = forecast  # 最终版（集成阶段读 forecast.json 文件，这里仅保留内存副本）
        # P2-4: 追加进校准账本（loop-closer；resolution 经 /api/v1/resolve 或 forecast_tools backtest）。
        if getattr(Config, "REPORT_FORECAST_LEDGER", True):
            try:
                from .forecast_ledger import append_forecast as _append
                _append(forecast, report_id=report_id,
                        horizon=str(forecast.get("horizon") or "") or None,
                        created_at=datetime.now().isoformat())
            except Exception:  # noqa: BLE001
                pass
        logger.info(
            f"结构化预测已生成: {report_id} "
            f"({len(forecast.get('scenarios', []))} 情景, "
            f"引用覆盖 {forecast['citation_audit'].get('coverage')}, "
            f"信心 {forecast.get('confidence')})"
        )

    def _append_resolution_section(self, report_id: str, report: "Report") -> None:
        """NEXTSTEPS P2-2: 把确定性的「如何验证本预测」章节（判定标准 + 观察指标）追加到成稿末尾
        并重写 full_report.md。指标-情景映射已随 forecast.json 落盘。仅在已有预测骨架时调用。
        """
        from .forecast_extractor import render_resolution_block
        from ..utils import actors as _actors
        try:
            indicators = _actors.extract_forecast_inputs(self.actors).get("indicators") or []
        except Exception:  # noqa: BLE001
            indicators = []
        block = render_resolution_block(self._forecast_spine, indicators)
        if not block:
            return
        new_md = (report.markdown_content or "").rstrip() + "\n\n" + block + "\n"
        report.markdown_content = new_md
        try:
            folder = ReportManager._get_report_folder(report_id)
            write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
        except Exception as _we:  # noqa: BLE001
            logger.warning(f"重写 full_report.md（追加判定标准章节）失败（忽略）: {_we}")
        logger.info(f"已追加判定标准与观察指标章节: {report_id}")

    @staticmethod
    def _apply_publish_gate(forecast: Dict[str, Any]) -> Dict[str, Any]:
        """NEXTSTEPS P2-3: coherence + grounding publish gate.

        A calibrated forecaster must refuse to silently publish incoherent or
        ungrounded probability sets. Checks citation coverage of quantitative claims,
        probability-sum coherence, presence of a residual/status-quo scenario, and
        degenerate entropy; records ``forecast['quality']`` and demotes ``confidence``
        (at most one level) when any issue is found. Pure / best-effort; never raises.
        """
        try:
            scenarios = forecast.get("scenarios") or []
            audit = forecast.get("citation_audit") or {}
            coverage = float(audit.get("coverage", 1.0) or 0.0)
            min_cov = float(getattr(Config, "REPORT_PUBLISH_GATE_MIN_COVERAGE", 0.5) or 0.0)
            probs: List[float] = []
            for s in scenarios:
                try:
                    probs.append(float(s.get("probability") or 0.0))
                except (TypeError, ValueError):
                    pass
            prob_sum = round(sum(probs), 3)
            _residual_keys = ("维持现状", "其它", "其他", "兜底", "status", "other", "baseline")
            has_residual = any(
                any(k in str(s.get("name", "")).lower() for k in _residual_keys)
                for s in scenarios
            )
            top = max(probs) if probs else 0.0
            issues: List[str] = []
            if scenarios and coverage < min_cov:
                issues.append(f"定量声明引用覆盖率 {coverage:.2f} < 阈值 {min_cov:.2f}")
            if scenarios and abs(prob_sum - 1.0) > 0.05:
                issues.append(f"情景概率之和 {prob_sum} 偏离 1")
            if scenarios and not has_residual:
                issues.append("缺少『维持现状/兜底』情景")
            if top >= 0.9 and len(probs) <= 1:
                issues.append("概率分布退化（单情景≥0.9 且无对照情景）")
            forecast["quality"] = {
                "citation_coverage": round(coverage, 3),
                "probability_sum": prob_sum,
                "has_residual_scenario": has_residual,
                "max_probability": round(top, 3),
                "issues": issues,
                "passed": not issues,
            }
            if issues:
                levels = ["low", "medium", "high"]
                order = {"low": 0, "medium": 1, "high": 2}
                cur = order.get(str(forecast.get("confidence", "medium")).lower(), 1)
                forecast["confidence"] = levels[max(0, cur - 1)]
                rationale = (forecast.get("confidence_rationale", "") or "").strip()
                forecast["confidence_rationale"] = (
                    (rationale + " ｜发布门：" + "；".join(issues)).strip(" ｜")
                )
        except Exception as _qe:  # noqa: BLE001 — 发布门为旁路品控，失败不影响产物
            logger.warning(f"发布门计算失败（忽略）: {_qe}")
        return forecast

    # ──────────────────────────────────────────────────────────────
    # EXECPLAN2 I-3-4: 结构化「基线 vs 情景」对比表（确定性，无 LLM）
    # 数据源与 zep_tools.scenario_diff 完全一致（SimulationRunner 的
    # get_timeline / get_agent_stats），保证「按构造即正确」，让 LLM 围绕
    # 权威表格叙述而非自行复算差值（避免反转 delta 符号或漏维度）。
    # ──────────────────────────────────────────────────────────────
    def _scenario_diff_structured(self) -> Optional[Dict[str, Any]]:
        """把基线/情景两次模拟归一化为可比维度的字典。

        返回 {dimensions:[{name, baseline, scenario, delta, verdict}], rounds_*}；
        无基线或两侧均无数据时返回 None（调用方自动跳过，行为不变）。
        """
        if not self.base_simulation_id:
            return None
        try:
            from .simulation_runner import SimulationRunner
            base_tl = SimulationRunner.get_timeline(self.base_simulation_id) or []
            scen_tl = SimulationRunner.get_timeline(self.simulation_id) or []
            base_stats = SimulationRunner.get_agent_stats(self.base_simulation_id) or []
            scen_stats = SimulationRunner.get_agent_stats(self.simulation_id) or []
        except Exception as e:  # noqa: BLE001 — 对比表为可选增强，失败返回 None 即跳过
            logger.warning(f"结构化对比表读取模拟数据失败（忽略）: {e}")
            return None
        if not (base_tl or scen_tl):
            return None

        def _total(tl):
            return sum(int(r.get("total_actions", 0)) for r in tl)

        def _peak(tl):
            return max(tl, key=lambda r: r.get("total_actions", 0), default=None)

        dims: List[Dict[str, Any]] = []

        # 维度1：总动作量（数值高低判定）
        bt, st = _total(base_tl), _total(scen_tl)
        pct = ((st - bt) / bt * 100) if bt else 0.0
        dims.append({
            "name": "总动作量",
            "baseline": bt,
            "scenario": st,
            "delta": f"{st - bt:+d}（{pct:+.1f}%）",
            "verdict": "更高" if st > bt else ("更低" if st < bt else "持平"),
        })

        # 维度2：峰值轮次（时间早晚判定 —— 情景峰值更早=更快爆发）
        bp, sp = _peak(base_tl), _peak(scen_tl)
        if bp and sp:
            br, sr = int(bp["round_num"]), int(sp["round_num"])
            dims.append({
                "name": "峰值轮次",
                "baseline": f"round {br}（{bp['total_actions']}）",
                "scenario": f"round {sr}（{sp['total_actions']}）",
                "delta": f"{sr - br:+d} 轮",
                "verdict": "更早" if sr < br else ("更晚" if sr > br else "同轮"),
            })

        # 维度3：执行轮数（升温/降温速度的代理）
        bl, sl = len(base_tl), len(scen_tl)
        dims.append({
            "name": "执行轮数",
            "baseline": bl,
            "scenario": sl,
            "delta": f"{sl - bl:+d}",
            "verdict": "更长" if sl > bl else ("更短" if sl < bl else "持平"),
        })

        # 维度4：参与 Agent 数（动员广度）
        b_agents = len(base_stats)
        s_agents = len(scen_stats)
        dims.append({
            "name": "参与 Agent 数",
            "baseline": b_agents,
            "scenario": s_agents,
            "delta": f"{s_agents - b_agents:+d}",
            "verdict": "更多" if s_agents > b_agents else ("更少" if s_agents < b_agents else "持平"),
        })

        # 维度5：变化最大的 Top mover（活跃度 delta 绝对值最大者）
        b_by = {s.get("agent_name"): int(s.get("total_actions", 0)) for s in base_stats}
        s_by = {s.get("agent_name"): int(s.get("total_actions", 0)) for s in scen_stats}
        names = [n for n in (set(b_by) | set(s_by)) if n]
        if names:
            top_name = max(names, key=lambda n: abs(s_by.get(n, 0) - b_by.get(n, 0)))
            d = s_by.get(top_name, 0) - b_by.get(top_name, 0)
            dims.append({
                "name": "活跃度变化最大 Agent",
                "baseline": f"{top_name}: {b_by.get(top_name, 0)}",
                "scenario": f"{top_name}: {s_by.get(top_name, 0)}",
                "delta": f"{d:+d}",
                "verdict": "升" if d > 0 else ("降" if d < 0 else "不变"),
            })

        return {
            "base_simulation_id": self.base_simulation_id,
            "scenario_simulation_id": self.simulation_id,
            "scenario_label": self.scenario_label,
            "dimensions": dims,
        }

    @staticmethod
    def _is_comparison_section(title: str) -> bool:
        """EXECPLAN2 I-3-4: 判定某章节是否为「情景对比 / 反事实」章节。

        plan_outline 在有基线时已强制大纲含标题含「情景对比」或「反事实」的章节，
        这里据此识别需要前置对比表的章节。"""
        t = (title or "")
        return ("情景对比" in t) or ("反事实" in t)

    @staticmethod
    def _render_comparison_table(diff_dict: Dict[str, Any]) -> str:
        """EXECPLAN2 I-3-4: 把结构化对比字典渲染为 GFM Markdown 表格（数据缺失的行自动跳过）。"""
        dims = diff_dict.get("dimensions") or []
        if not dims:
            return ""

        def _esc(v: Any) -> str:
            # 表格单元转义竖线/换行，避免破坏 Markdown 表格结构
            return str(v).replace("|", "\\|").replace("\n", " ").strip()

        scen_label = (diff_dict.get("scenario_label") or "").strip()
        scen_col = f"情景（{scen_label}）" if scen_label else "情景"
        lines = [
            "**基线 vs 情景 结构化对比（确定性聚合，权威）**",
            "",
            f"| 维度 | 基线 | {_esc(scen_col)} | Δ | 判定 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for d in dims:
            lines.append(
                f"| {_esc(d.get('name'))} | {_esc(d.get('baseline'))} | "
                f"{_esc(d.get('scenario'))} | {_esc(d.get('delta'))} | {_esc(d.get('verdict'))} |"
            )
        lines.append("")
        lines.append("> 上表为确定性聚合结果，正文请围绕这些权威差值展开解读，勿自行复算或反转方向。")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────
    # EXECPLAN2 I-5-4: 报告级 LLM 成本/时延遥测（per-section + totals）
    # 复用中央 LLMMeter（按 run_id=report_id 聚合）。每章节前后各取一次累计快照，
    # 相减即为该章节触发的 LLM 经济学（tokens / cost / latency / calls）。
    # 全程 degrade-safe：未开 LLM_TELEMETRY_ENABLED & REPORT_TELEMETRY 时不调用本逻辑。
    # ──────────────────────────────────────────────────────────────
    def _telemetry_enabled(self) -> bool:
        """两个开关都为真才采集：底层计量已开（LLM_TELEMETRY_ENABLED），且报告级汇总已开。"""
        return bool(
            getattr(Config, "LLM_TELEMETRY_ENABLED", False)
            and getattr(Config, "REPORT_TELEMETRY", False)
        )

    def _meter_total(self, run_id: str) -> Dict[str, Any]:
        """读取本 run 的累计 LLM 计量 total（失败返回零值，绝不抛出）。

        用于 per-section 区间差值：章节循环期间 stage 恒为 'report'，故 total 的增量即本章节
        触发的 LLM 经济学（哪怕 run_id 与上游共享，区间差值仍只含本报告期间的调用）。
        """
        try:
            return dict(LLMMeter.snapshot(run_id).get("total") or {})
        except Exception:  # noqa: BLE001 — 遥测读取失败不得影响报告生成
            return {}

    def _meter_stage_total(self, run_id: str, stage: str = "report") -> Dict[str, Any]:
        """读取本 run 中指定 stage 的累计 LLM 计量（失败返回零值，绝不抛出）。

        用于报告级 totals：当 run_id 是上游编排器的共享 run（如 pipeline_id）时，total 会混入
        其它阶段（research/ontology/...）的调用；按 stage='report' 切片可只统计报告阶段花销。
        """
        try:
            by_stage = LLMMeter.snapshot(run_id).get("by_stage") or {}
            return dict(by_stage.get(stage) or {})
        except Exception:  # noqa: BLE001 — 遥测读取失败不得影响报告生成
            return {}

    @staticmethod
    def _meter_delta(before: Dict[str, Any], after: Dict[str, Any], duration_s: float,
                     tool_calls: int) -> Dict[str, Any]:
        """两次 total 快照相减，得到一段区间内的紧凑遥测条目。"""
        def _g(d: Dict[str, Any], k: str) -> float:
            try:
                return float(d.get(k, 0) or 0)
            except (TypeError, ValueError):
                return 0.0
        llm_calls = int(_g(after, "calls") - _g(before, "calls"))
        tokens = int(_g(after, "total_tokens") - _g(before, "total_tokens"))
        cost = round(_g(after, "cost_usd") - _g(before, "cost_usd"), 6)
        latency_ms = round(_g(after, "latency_ms") - _g(before, "latency_ms"), 1)
        return {
            "llm_calls": max(0, llm_calls),
            "tool_calls": max(0, int(tool_calls)),
            "tokens": max(0, tokens),
            "est_cost_usd": max(0.0, cost),
            "latency_ms": max(0.0, latency_ms),
            "duration_s": round(max(0.0, duration_s), 2),
        }


    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """定义可用工具"""
        tools = {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "你想深入分析的问题或话题",
                    "report_context": "当前报告章节的上下文（可选，有助于生成更精准的子问题）",
                    "as_of": "（可选）时点视图：只检索在该日期成立的事实（YYYY-MM-DD 或年份）。"
                             "用于论证立场/关系随时间的漂移——对比种子时点 vs 模拟终局，"
                             "而非只看时间压平后的快照。"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "搜索查询，用于相关性排序",
                    "include_expired": "是否包含过期/历史内容（默认True）"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "搜索查询字符串",
                    "limit": "返回结果数量（可选，默认10）"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "采访主题或需求描述（如：'了解学生对宿舍甲醛事件的看法'）",
                    "max_agents": "最多采访的Agent数量（可选，默认5，最大6）"
                }
            },
            # T4.2: 结构化模拟结果工具（量化论断必须有据可查）
            "simulation_outcomes": {
                "name": "simulation_outcomes",
                "description": "获取模拟的量化结果：最活跃 Agent 排名、逐轮动作量、动作类型分布、峰值轮次。任何涉及『谁最活跃/参与度/动作量/级联峰值』的量化论断都应先调用它，引用具体数字。",
                "parameters": {"top_n": "返回最活跃 Agent 的数量（可选，默认15）"}
            },
            "coalition_map": {
                "name": "coalition_map",
                "description": "派系/联盟图：把对相同对象互动（点赞/转发/评论/关注）的 Agent 聚成派系（确定性，无需猜测）。分析阵营/联盟/抱团结构时使用。",
                "parameters": {}
            },
            "opinion_shift": {
                "name": "opinion_shift",
                "description": "单个 Agent/角色的逐轮行为轨迹（动作量/类型随轮次变化），用于观察其立场或参与度的演变。",
                "parameters": {"actor_name": "要追踪的 Agent/角色名"}
            },
            # NEXTSTEPS P3-6: 图谱多跳传导/级联追踪（结构推理，互补于 1-hop 检索）
            "trace_cascade": {
                "name": "trace_cascade",
                "description": "多跳传导/级联追踪：给 source+target 列出图谱中二者间的有向路径（优先因果边 "
                               "CAUSES/ENABLES/CONSTRAINS/TRIGGERS/ACCELERATES）；只给 center 列出其多跳因果邻域。"
                               "用于『追踪级联、哪个节点一动就翻盘』的结构推理（而非 1-hop 检索）。",
                "parameters": {"source": "起点实体名（与 target 配对追路径）",
                               "target": "终点实体名",
                               "center": "（可选）中心实体名：只看其多跳传导邻域时用"}
            }
        }
        # I-1-2: faction_brief 仅在启用图谱社区检索时暴露（默认关 → 工具集与现状逐字节一致）。
        # 图谱原生派系证据（社区检测 + LLM 摘要），无社区节点时内部降级到 coalition_map。
        if Config.GRAPH_COMMUNITY_RETRIEVAL:
            tools["faction_brief"] = {
                "name": "faction_brief",
                "description": "派系简报：基于图谱社区检测(Leiden)+LLM 摘要，列出各派系的成员实体与定位。分析阵营/联盟/对立结构时优先于 coalition_map（图谱原生证据，互补于行为日志聚类）。",
                "parameters": {"query": "（可选）聚焦的主题/实体关键词"}
            }
        # T4.7: 仅情景报告（有基线模拟）暴露反事实对比工具
        if self.base_simulation_id:
            tools["scenario_diff"] = {
                "name": "scenario_diff",
                "description": "反事实对比：基线模拟 vs 当前情景模拟的结构化差异（总动作量/峰值轮次/逐轮 delta/Top-actor 活跃度 delta）。撰写『情景对比』章节时必须调用，引用具体差值。",
                "parameters": {}
            }
        return tools

    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        执行工具调用
        
        Args:
            tool_name: 工具名称
            parameters: 工具参数
            report_context: 报告上下文（用于InsightForge）
            
        Returns:
            工具执行结果（文本格式）
        """
        logger.info(f"执行工具: {tool_name}, 参数: {parameters}")
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                # NEXTSTEPS P0-2: 时点视图（可选）。底层 insight_forge 早已支持 as_of，但此前
                # 工具 schema/分发从不暴露它 → 预测者只能看到时间压平的快照。这里把它接通。
                as_of = parameters.get("as_of") or None
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx,
                    as_of=as_of,
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # 广度搜索 - 获取全貌
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # 简单搜索 - 快速检索
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # 深度采访 - 调用真实的OASIS采访API获取模拟Agent的回答（双平台）
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                # 每位受访者都是一次双平台 claude-cli 采访（~14-40s）。把上限收紧到 6，
                # 让单次批量采访稳稳落在 600s 的 IPC 超时预算内（曾经的 8 人会触发超时）。
                max_agents = max(1, min(max_agents, 6))
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents,
                    graph_id=self.graph_id,  # T3.14: 把采访回答持久化为 typed 图谱事实
                )
                return result.to_text()
            
            elif tool_name == "simulation_outcomes":
                top_n = parameters.get("top_n", 15)
                if isinstance(top_n, str):
                    top_n = int(top_n) if top_n.isdigit() else 15
                return self.zep_tools.simulation_outcomes(self.simulation_id, top_n=top_n)

            elif tool_name == "coalition_map":
                return self.zep_tools.coalition_map(self.graph_id, self.simulation_id)

            elif tool_name == "faction_brief":  # EXECPLAN2 I-1-2
                return self.zep_tools.faction_brief(
                    self.graph_id, parameters.get("query", ""), self.simulation_id)

            elif tool_name == "opinion_shift":
                actor_name = parameters.get("actor_name", parameters.get("query", ""))
                return self.zep_tools.opinion_shift(self.simulation_id, actor_name)

            elif tool_name == "trace_cascade":  # NEXTSTEPS P3-6: 多跳传导/级联追踪
                return self.zep_tools.trace_cascade(
                    graph_id=self.graph_id,
                    source=parameters.get("source", ""),
                    target=parameters.get("target", ""),
                    center=parameters.get("center", ""),
                )

            elif tool_name == "scenario_diff":
                # T4.7: 反事实对比 base vs 当前情景模拟
                if not self.base_simulation_id:
                    return "（本报告非情景对比报告，无基线模拟可对比）"
                return self.zep_tools.scenario_diff(self.base_simulation_id, self.simulation_id)

            # ========== 向后兼容的旧工具（内部重定向到新工具） ==========

            elif tool_name == "search_graph":
                # 重定向到 quick_search
                logger.info("search_graph 已重定向到 quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # 重定向到 insight_forge，因为它更强大
                logger.info("get_simulation_context 已重定向到 insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"未知工具: {tool_name}。请使用以下工具之一: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(f"工具执行失败: {tool_name}, 错误: {str(e)}")
            return f"工具执行失败: {str(e)}"
    
    # 向后兼容的旧工具别名（_execute_tool 内部重定向到新工具，不在 self.tools 中暴露）。
    # 单独维护，避免与动态工具集（_define_tools 按 Config 条件构建）漂移。
    _LEGACY_TOOL_ALIASES = {"search_graph", "get_graph_statistics", "get_entity_summary",
                            "get_simulation_context", "get_entities_by_type"}

    # 合法的工具名称集合（遗留符号）：保留为旧工具别名集合，避免外部引用 NameError。
    # 实际校验改用 self._valid_tool_names()，从 live 工具集动态派生，杜绝与 self.tools 漂移。
    VALID_TOOL_NAMES = _LEGACY_TOOL_ALIASES

    def _valid_tool_names(self) -> set:
        """从当前实例的 live 工具集动态派生合法工具名，叠加向后兼容的旧工具别名。

        self.tools 由 _define_tools 按 Config 条件构建（如 faction_brief 仅在
        GRAPH_COMMUNITY_RETRIEVAL 时存在，scenario_diff 仅在情景报告时存在），
        因此校验必须以 live 工具集为准，而非静态名单——否则条件工具会在裸 JSON 兜底
        解析时被误丢。默认（条件工具关）时该集合与历史静态名单逐字节等价。
        """
        return set(self.tools.keys()) | self._LEGACY_TOOL_ALIASES

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLM响应中解析工具调用

        支持的格式（按优先级）：
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. 裸 JSON（响应整体或单行就是一个工具调用 JSON）
        """
        tool_calls = []

        # 格式1: XML风格（标准格式）
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # 格式2: 兜底 - LLM 直接输出裸 JSON（没包 <tool_call> 标签）
        # 只在格式1未匹配时尝试，避免误匹配正文中的 JSON
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # 响应可能包含思考文字 + 裸 JSON，尝试提取最后一个 JSON 对象
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """校验解析出的 JSON 是否是合法的工具调用"""
        # 支持 {"name": ..., "parameters": ...} 和 {"tool": ..., "params": ...} 两种键名
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self._valid_tool_names():
            # 统一键名为 name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """生成工具描述文本"""
        desc_parts = ["可用工具："]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  参数: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        规划报告大纲
        
        使用LLM分析模拟需求，规划报告的目录结构
        
        Args:
            progress_callback: 进度回调函数
            
        Returns:
            ReportOutline: 报告大纲
        """
        logger.info("开始规划报告大纲...")
        
        if progress_callback:
            progress_callback("planning", 0, "正在分析模拟需求...")
        
        # 首先获取模拟上下文
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "正在生成报告大纲...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            # T4.3: 事实切片 10→25，给规划更充足的实证依据。
            related_facts_json=json.dumps(context.get('related_facts', [])[:25], ensure_ascii=False, indent=2),
        )
        # T4.1: 钉入研究背景档案 + 来源索引，让大纲规划基于真实 cast/关系，而非纯凭模拟统计盲设。
        user_prompt = self._prepend_research_background(user_prompt)

        # T4.3: 规划前先做两次扫描——一次图谱深挖（central_question）+ 一次结构化模拟结果，
        # 让大纲随真实研究发现与模拟动态而变（如高级联的运行会催生「级联」章节），而非盲设。
        sweeps = []
        try:
            forge = self.zep_tools.insight_forge(
                graph_id=self.graph_id,
                query=self.simulation_requirement,
                simulation_requirement=self.simulation_requirement,
                report_context="报告大纲规划阶段的全局扫描",
            )
            forge_text = forge.to_text() if hasattr(forge, "to_text") else str(forge)
            if forge_text:
                sweeps.append("【图谱深挖摘要】\n" + forge_text[:3000])
        except Exception as e:
            logger.warning(f"plan_outline insight_forge 扫描失败（忽略）: {e}")
        try:
            outcomes = self.zep_tools.simulation_outcomes(self.simulation_id, top_n=10)
            if outcomes:
                sweeps.append("【模拟量化结果摘要】\n" + outcomes[:2500])
        except Exception as e:
            logger.warning(f"plan_outline simulation_outcomes 扫描失败（忽略）: {e}")
        if sweeps:
            user_prompt = user_prompt + "\n\n" + "\n\n".join(sweeps)

        # T4.7: 情景对比报告 —— 强制大纲包含「情景对比 / 反事实」章节，并预取 scenario_diff 摘要
        if self.base_simulation_id:
            try:
                diff_text = self.zep_tools.scenario_diff(self.base_simulation_id, self.simulation_id)
                if diff_text:
                    user_prompt += "\n\n【基线 vs 情景 结构化对比（必须据此撰写对比章节）】\n" + diff_text[:2500]
            except Exception as e:
                logger.warning(f"plan_outline scenario_diff 扫描失败（忽略）: {e}")
            user_prompt += (
                "\n\n**强制要求**：本报告为情景（What-If）预测，大纲必须包含一节标题含"
                "「情景对比」或「反事实」的章节，对比基线与本情景的关键差异（引用上面对比数据中的具体差值）。"
            )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "正在解析大纲结构...")
            
            # 解析大纲
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content="",
                    description=section_data.get("description", "")
                ))

            # 钳制到 5-8 节契约（PLAN_SYSTEM_PROMPT 的硬约束）：不足补齐，超出截断。
            # LLM 偶尔会无视数量要求，这里兜底以保证下游章节生成数量稳定。
            if len(sections) < self.OUTLINE_MIN_SECTIONS:
                _existing = {s.title for s in sections}
                for _title in self._FALLBACK_SECTION_TITLES:
                    if len(sections) >= self.OUTLINE_MIN_SECTIONS:
                        break
                    if _title in _existing:
                        continue
                    sections.append(ReportSection(title=_title))
                    _existing.add(_title)
                logger.warning(
                    f"大纲章节数不足 {self.OUTLINE_MIN_SECTIONS}，已补齐至 {len(sections)} 节"
                )
            elif len(sections) > self.OUTLINE_MAX_SECTIONS:
                logger.warning(
                    f"大纲章节数 {len(sections)} 超过上限 {self.OUTLINE_MAX_SECTIONS}，已截断"
                )
                sections = sections[:self.OUTLINE_MAX_SECTIONS]

            outline = ReportOutline(
                title=response.get("title", "模拟分析报告"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "大纲规划完成")
            
            logger.info(f"大纲规划完成: {len(sections)} 个章节")
            return outline
            
        except Exception as e:
            logger.error(f"大纲规划失败: {str(e)}")
            # 返回默认大纲（5个章节，满足 5-8 节契约的下限，作为 fallback）
            return ReportOutline(
                title="未来预测报告",
                summary="基于模拟预测的未来趋势与风险分析",
                sections=[
                    ReportSection(title=_title) for _title in self._FALLBACK_SECTION_TITLES
                ]
            )
    
    # ---- EXECPLAN2 I-6-3: concurrent section generation helpers ----
    _TAIL_SECTION_RE = re.compile(
        r"(总结|结论|结语|执行摘要|概要|summary|conclusion|executive)", re.I
    )

    @classmethod
    def _is_tail_section(cls, title: str) -> bool:
        """True for summary/conclusion-style sections that depend on the body and
        must be generated LAST (sequentially, with full body text). Pure."""
        return bool(cls._TAIL_SECTION_RE.search(title or ""))

    @staticmethod
    def _build_synthesis_brief(sections: List[ReportSection]) -> str:
        """Compact 'synthesis brief' (outline + per-section 1-liner) used as shared
        cross-section context when sections are generated concurrently or in brief
        context mode — replaces the O(N²) 'full text of every prior section'. Pure."""
        lines = ["【报告大纲与各章节意图（用于保持全局一致性）】"]
        for i, s in enumerate(sections, 1):
            desc = (getattr(s, "description", "") or "").strip()
            lines.append(f"{i}. {s.title}" + (f"：{desc}" if desc else ""))
        return "\n".join(lines)

    def _generate_sections_concurrent(self, outline: "ReportOutline", concurrency: int) -> Dict[int, str]:
        """Pre-generate all section contents with bounded concurrency, returning
        {section_index: content}. Body sections (independent) run in a thread pool
        with the shared synthesis brief as context; summary/conclusion sections run
        last, sequentially, with the full body text for coherence. Per-section
        failures degrade to the placeholder (never abort the report). Caller's serial
        bookkeeping loop then consumes the returned contents in order.
        """
        import concurrent.futures as _cf

        sections = outline.sections
        body = [(i, s) for i, s in enumerate(sections) if not self._is_tail_section(s.title)]
        tail = [(i, s) for i, s in enumerate(sections) if self._is_tail_section(s.title)]
        if not body:  # everything looked like a summary → treat all as body (keep parallelism)
            body, tail = tail, []
        brief = self._build_synthesis_brief(sections)
        contents: Dict[int, str] = {}

        def _noop(*_a, **_k):
            return None

        def _gen_body(idx: int, section: ReportSection):
            try:
                return idx, self._generate_section(
                    section=section, outline=outline, previous_sections=[brief],
                    progress_callback=_noop, section_index=idx + 1)
            except Exception as e:  # noqa: BLE001 — per-section isolation
                logger.error(f"并发章节生成异常（降级占位符）: {section.title} -> {e}")
                return idx, SECTION_FAILURE_PLACEHOLDER

        if body:
            with _cf.ThreadPoolExecutor(max_workers=min(concurrency, len(body))) as ex:
                for fut in _cf.as_completed([ex.submit(_gen_body, i, s) for i, s in body]):
                    idx, content = fut.result()
                    contents[idx] = content

        # tail sections: sequential, full body text as context for narrative closure
        body_text = [f"## {sections[i].title}\n\n{contents.get(i, '')}" for i, _ in body]
        for idx, section in tail:
            try:
                contents[idx] = self._generate_section(
                    section=section, outline=outline, previous_sections=body_text,
                    progress_callback=_noop, section_index=idx + 1)
            except Exception as e:  # noqa: BLE001
                logger.error(f"尾部章节生成异常（降级占位符）: {section.title} -> {e}")
                contents[idx] = SECTION_FAILURE_PLACEHOLDER
            body_text.append(f"## {section.title}\n\n{contents[idx]}")
        return contents

    def _generate_section(
        self,
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0,
    ) -> str:
        """T4.5: 章节生成调度——原生 tool calling 可用时走结构化路径，否则回退手搓 ReAct。"""
        if self.llm.supports_native_tools():
            try:
                return self._generate_section_native(
                    section, outline, previous_sections, progress_callback, section_index
                )
            except Exception as e:  # noqa: BLE001 — 原生路径异常时优雅回退 ReAct，绝不让章节失败
                logger.warning(f"原生 tool calling 章节生成失败，回退 ReAct: {e}")
        return self._generate_section_react(
            section, outline, previous_sections, progress_callback, section_index
        )

    def _to_openai_tool_schemas(self) -> List[Dict[str, Any]]:
        """T4.5: 把内部 tools 定义转成 OpenAI function tool schema。

        直接遍历 live 工具集 self.tools（_define_tools 按 Config 条件构建），使
        faction_brief / scenario_diff 等条件工具在被定义时即原生暴露，杜绝「prompt 中
        宣告但 tools= schema 缺失」的漂移。旧工具别名是 _execute_tool 的内部重定向，
        本就不应原生暴露，故不纳入。默认（条件工具关）时输出与历史静态名单逐字节一致。
        """
        schemas = []
        for tname in sorted(self.tools.keys()):
            spec = self.tools.get(tname)
            if not spec:
                continue
            props = {}
            for pname, pdesc in (spec.get("parameters") or {}).items():
                props[pname] = {"type": "string", "description": str(pdesc)}
            schemas.append({
                "type": "function",
                "function": {
                    "name": tname,
                    "description": spec.get("description", tname),
                    "parameters": {"type": "object", "properties": props},
                },
            })
        return schemas

    def _generate_section_native(
        self,
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0,
    ) -> str:
        """T4.5: 用原生 tool calling 生成章节（无正则解析/无 conflict_retries/无污染检测）。"""
        logger.info(f"原生 tool calling 生成章节: {section.title}")
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)

        _section_heading = section.title
        if section.description:
            _section_heading = f"{section.title}\n本章内容定位（大纲规划）: {section.description}"
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=_section_heading,
            tools_description=self._get_tools_description(),
        )
        system_prompt = self._prepend_research_background(system_prompt)
        # 原生路径：覆盖 ReAct 的格式要求，改为「自然调用工具，最后直接输出 Markdown 正文」
        system_prompt += (
            "\n\n【输出模式】你已具备原生工具调用能力：需要数据时直接发起工具调用（可多次），"
            "信息充分后直接输出本章 Markdown 正文（不要输出 Thought/Action/Final Answer 等标记，"
            "不要输出 JSON 工具包裹）。撰写正文前至少调用 "
            f"{self.MIN_TOOL_CALLS_PER_SECTION} 次工具以获取实证。"
        )

        if previous_sections:
            previous_content = "\n\n---\n\n".join(
                (sec[:8000] + "..." if len(sec) > 8000 else sec) for sec in previous_sections
            )
        else:
            previous_content = "（这是第一个章节）"
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content, section_title=section.title
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        schemas = self._to_openai_tool_schemas()
        max_iterations = 10
        max_tool_calls = self.MAX_TOOL_CALLS_PER_SECTION
        tool_calls_count = 0

        for _ in range(max_iterations):
            resp = self.llm.chat_with_tools(
                messages, schemas, temperature=Config.REPORT_AGENT_TEMPERATURE
            )
            calls = resp.get("tool_calls") or []
            content = resp.get("content") or ""

            if calls and tool_calls_count < max_tool_calls:
                # 回填 assistant 工具调用消息
                messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                        for c in calls
                    ],
                })
                for c in calls:
                    tool_calls_count += 1
                    self._section_tool_calls += 1  # EXECPLAN2 I-5-4: per-section 工具调用计数
                    try:
                        result = self._execute_tool(c["name"], c["arguments"], report_context=section.title)
                    except Exception as te:  # noqa: BLE001
                        result = f"（工具 {c['name']} 执行失败：{te}）"
                    if self.report_logger:
                        try:
                            self.report_logger.log_tool_call(
                                section.title, section_index, c["name"], c["arguments"], tool_calls_count
                            )
                        except Exception:
                            pass
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": str(result)[:8000]})
                if progress_callback:
                    progress_callback("generating", min(90, tool_calls_count * 12), f"{section.title}: 工具检索 {tool_calls_count}")
                continue

            # 无更多工具调用（或已达上限）→ 收尾出正文
            if content.strip():
                # EXECPLAN2 F-7-2 工具调用不足且仍可继续检索 → 拒绝过早出正文，强制补足实证（对齐 ReAct 路径）
                if tool_calls_count < self.MIN_TOOL_CALLS_PER_SECTION and tool_calls_count < max_tool_calls:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"你只调用了 {tool_calls_count} 次工具，少于本章要求的至少 "
                            f"{self.MIN_TOOL_CALLS_PER_SECTION} 次。请勿现在输出正文，"
                            "继续发起工具调用以补足实证后再撰写本章。"
                        ),
                    })
                    continue
                return content
            # 达到工具上限但模型还没出正文：显式要求收尾
            messages.append({"role": "user", "content": "请基于以上工具结果直接输出本章完整 Markdown 正文。"})

        # 兜底：迭代用尽仍无正文 → 末次无工具强制出文
        final = self.llm.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt + "\n\n请直接输出本章 Markdown 正文。"},
        ], temperature=Config.REPORT_AGENT_TEMPERATURE, max_tokens=4096)
        return final

    def _generate_section_react(
        self,
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        使用ReACT模式生成单个章节内容
        
        ReACT循环：
        1. Thought（思考）- 分析需要什么信息
        2. Action（行动）- 调用工具获取信息
        3. Observation（观察）- 分析工具返回结果
        4. 重复直到信息足够或达到最大次数
        5. Final Answer（最终回答）- 生成章节内容
        
        Args:
            section: 要生成的章节
            outline: 完整大纲
            previous_sections: 之前章节的内容（用于保持连贯性）
            progress_callback: 进度回调
            section_index: 章节索引（用于日志记录）
            
        Returns:
            章节内容（Markdown格式）
        """
        logger.info(f"ReACT生成章节: {section.title}")
        
        # 记录章节开始日志
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        # 规划阶段的章节描述拼进章节标题位，让撰写贴合大纲意图（模板无独立槽位，
        # 避免对超长模板做侵入式修改）
        _section_heading = section.title
        if section.description:
            _section_heading = f"{section.title}\n本章内容定位（大纲规划）: {section.description}"
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=_section_heading,
            tools_description=self._get_tools_description(),
        )
        # T4.1: 钉入研究背景档案 + 来源索引，让每章撰写复用真实角色/关系/时间线并按 [S#] 引用。
        system_prompt = self._prepend_research_background(system_prompt)

        # 构建用户prompt - 每个已完成章节各传入最大4000字
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # 每个已完成章节最多保留 8000 字作为上下文（避免重复、保持连贯）
                truncated = sec[:8000] + "..." if len(sec) > 8000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "（这是第一个章节）"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT循环
        tool_calls_count = 0
        max_iterations = 10  # 最大迭代轮数（更高以支撑更深入的检索与更长的章节）
        min_tool_calls = self.MIN_TOOL_CALLS_PER_SECTION  # T4.4: 从 Config 读取（默认 4）
        conflict_retries = 0  # 工具调用与Final Answer同时出现的连续冲突次数
        contamination_retries = 0  # 输出被污染（系统提示泄漏/工具调用残留）的连续重试次数
        MAX_CONTAMINATION_RETRIES = 2  # 污染输出最多纠正重试次数
        used_tools = set()  # 记录已调用过的工具名
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents",
                     "simulation_outcomes", "coalition_map", "opinion_shift"}
        if self.base_simulation_id:
            all_tools.add("scenario_diff")  # T4.7

        # 报告上下文，用于InsightForge的子问题生成
        report_context = f"章节标题: {section.title}\n模拟需求: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"深度检索与撰写中 ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # 调用LLM（提高 max_tokens 以容纳更长的章节正文；对 OpenAI 兼容提供方生效，
            # CLI 提供方由 prompt 篇幅下限驱动）
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=8192
            )

            # 检查 LLM 返回是否为 None（API 异常或内容为空）
            if response is None:
                logger.warning(f"章节 {section.title} 第 {iteration + 1} 次迭代: LLM 返回 None")
                # 如果还有迭代次数，添加消息并重试
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "（响应为空）"})
                    messages.append({"role": "user", "content": "请继续生成内容。"})
                    continue
                # 最后一次迭代也返回 None，跳出循环进入强制收尾
                break

            logger.debug(f"LLM响应: {response[:200]}...")

            # 解析一次，复用结果
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── 冲突处理：LLM 同时输出了工具调用和 Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"章节 {section.title} 第 {iteration+1} 轮: "
                    f"LLM 同时输出工具调用和 Final Answer（第 {conflict_retries} 次冲突）"
                )

                if conflict_retries <= 2:
                    # 前两次：丢弃本次响应，要求 LLM 重新回复
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【格式错误】你在一次回复中同时包含了工具调用和 Final Answer，这是不允许的。\n"
                            "每次回复只能做以下两件事之一：\n"
                            "- 调用一个工具（输出一个 <tool_call> 块，不要写 Final Answer）\n"
                            "- 输出最终内容（以 'Final Answer:' 开头，不要包含 <tool_call>）\n"
                            "请重新回复，只做其中一件事。"
                        ),
                    })
                    continue
                else:
                    # 第三次：降级处理，截断到第一个工具调用，强制执行
                    logger.warning(
                        f"章节 {section.title}: 连续 {conflict_retries} 次冲突，"
                        "降级为截断执行第一个工具调用"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # 记录 LLM 响应日志
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── 情况1：LLM 输出了 Final Answer ──
            if has_final_answer:
                # 工具调用次数不足，拒绝并要求继续调工具
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"（这些工具还未使用，推荐用一下他们: {', '.join(unused_tools)}）" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # 正常结束
                final_answer = response.split("Final Answer:")[-1].strip()

                # 即使带了 "Final Answer:"，正文仍可能被污染（claude-cli 偶尔在前缀后泄漏系统提示）。
                # 检测到污染则纠正重试，绝不直接采纳。
                if _looks_contaminated(final_answer):
                    if contamination_retries < MAX_CONTAMINATION_RETRIES:
                        contamination_retries += 1
                        logger.warning(
                            f"章节 {section.title} 的 Final Answer 疑似被污染，纠正重试 "
                            f"{contamination_retries}/{MAX_CONTAMINATION_RETRIES}"
                        )
                        messages.append({"role": "assistant", "content": response})
                        messages.append({"role": "user", "content": REACT_CONTAMINATED_RETRY_MSG})
                        continue
                    logger.error(
                        f"章节 {section.title} 的 Final Answer 多次被污染，写入失败占位符"
                    )
                    return SECTION_FAILURE_PLACEHOLDER

                logger.info(f"章节 {section.title} 生成完成（工具调用: {tool_calls_count}次）")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── 情况2：LLM 尝试调用工具 ──
            if has_tool_calls:
                # 工具额度已耗尽 → 明确告知，要求输出 Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # 只执行第一个工具调用
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"LLM 尝试调用 {len(tool_calls)} 个工具，只执行第一个: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])
                self._section_tool_calls += 1  # EXECPLAN2 I-5-4: per-section 工具调用计数

                # 构建未使用工具提示
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── 情况3：既没有工具调用，也没有 Final Answer ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # 工具调用次数不足，推荐未用过的工具
                unused_tools = all_tools - used_tools
                unused_hint = f"（这些工具还未使用，推荐用一下他们: {', '.join(unused_tools)}）" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # 工具调用已足够，LLM 输出了内容但没带 "Final Answer:" 前缀。
            # 历史上这里直接采纳原始输出，导致 claude-cli 的系统提示泄漏/工具调用残留被写进章节。
            # 现在先做污染检测：被污染则纠正重试；多次仍失败则写占位符而非垃圾内容。
            final_answer = response.strip()
            if _looks_contaminated(final_answer):
                if contamination_retries < MAX_CONTAMINATION_RETRIES:
                    contamination_retries += 1
                    logger.warning(
                        f"章节 {section.title} 无 'Final Answer:' 且输出疑似被污染，纠正重试 "
                        f"{contamination_retries}/{MAX_CONTAMINATION_RETRIES}"
                    )
                    # 此前已在情况3入口处把 assistant response 追加进 messages，这里只补纠正提示。
                    messages.append({"role": "user", "content": REACT_CONTAMINATED_RETRY_MSG})
                    continue
                logger.error(
                    f"章节 {section.title} 多次输出被污染且无 'Final Answer:'，写入失败占位符"
                )
                return SECTION_FAILURE_PLACEHOLDER

            logger.info(f"章节 {section.title} 未检测到 'Final Answer:' 前缀，直接采纳LLM输出作为最终内容（工具调用: {tool_calls_count}次）")

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # 达到最大迭代次数，强制生成内容
        logger.warning(f"章节 {section.title} 达到最大迭代次数，强制生成")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})

        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=8192
        )

        # 检查强制收尾时 LLM 返回是否为 None
        if response is None:
            logger.error(f"章节 {section.title} 强制收尾时 LLM 返回 None，使用默认错误提示")
            final_answer = f"（本章节生成失败：LLM 返回空响应，请稍后重试）"
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response

        # 强制收尾也可能吐出被污染的内容：宁可写占位符，也不把系统提示泄漏/工具残留写进报告。
        if _looks_contaminated(final_answer):
            logger.error(f"章节 {section.title} 强制收尾输出仍被污染，写入失败占位符")
            final_answer = SECTION_FAILURE_PLACEHOLDER

        # 记录章节内容生成完成日志
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        生成完整报告（分章节实时输出）
        
        每个章节生成完成后立即保存到文件夹，不需要等待整个报告完成。
        文件结构：
        reports/{report_id}/
            meta.json       - 报告元信息
            outline.json    - 报告大纲
            progress.json   - 生成进度
            section_01.md   - 第1章节
            section_02.md   - 第2章节
            ...
            full_report.md  - 完整报告
        
        Args:
            progress_callback: 进度回调函数 (stage, progress, message)
            report_id: 报告ID（可选，如果不传则自动生成）
            
        Returns:
            Report: 完整报告
        """
        import uuid
        
        # 如果没有传入 report_id，则自动生成
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # 已完成的章节标题列表（用于进度追踪）
        completed_section_titles = []

        # EXECPLAN2 I-5-4: 报告级遥测 —— 设定 LLM 计量上下文的 stage='report'。
        # run_id 优先沿用编排器已设定的 run（如 pipeline_id，便于跨阶段汇总）；独立生成
        # （无上游 run）时退回 report_id，使手动报告也被计量。保存旧上下文，finally 中还原，
        # 避免污染复用线程/调用方。默认关闭时不动上下文。
        _telemetry_on = self._telemetry_enabled()
        _prev_run_ctx = None
        _telemetry_run_id = report_id  # 实际用于 LLMMeter 快照的 run 键
        _report_stage_before: Dict[str, Any] = {}  # 报告开始时 stage='report' 的基线快照
        section_rollup: List[Dict[str, Any]] = []  # I-5-4: per-section 遥测条目
        if _telemetry_on:
            try:
                _prev_run_ctx = get_run_context()
                # 沿用上游 run_id（若已设），否则用 report_id；stage 统一标记为 'report'
                _telemetry_run_id = (_prev_run_ctx[0] if _prev_run_ctx and _prev_run_ctx[0] else report_id)
                set_run_context(_telemetry_run_id, "report")
                # 基线：共享 run 上可能已有其它报告的 report 阶段花销，取差值才是本报告真实花销
                _report_stage_before = self._meter_stage_total(_telemetry_run_id, "report")
            except Exception:  # noqa: BLE001 — 遥测初始化失败不得影响报告生成
                _telemetry_on = False
                _telemetry_run_id = report_id

        try:
            # 初始化：创建报告文件夹并保存初始状态
            ReportManager._ensure_report_folder(report_id)
            
            # 初始化日志记录器（结构化日志 agent_log.jsonl）
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # 初始化控制台日志记录器（console_log.txt）
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "初始化报告...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # 阶段1: 规划大纲
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "开始规划报告大纲...",
                completed_sections=[]
            )
            
            # 记录规划开始日志
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "开始规划报告大纲...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # 记录规划完成日志
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # 保存大纲到文件
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"大纲规划完成，共{len(outline.sections)}个章节",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"大纲已保存到文件: {report_id}/outline.json")
            
            # 阶段2: 逐章节生成（分章节保存）
            report.status = ReportStatus.GENERATING

            # EXECPLAN2 I-3-2: 构建一次模拟量化信号包，钉进随后每个章节提示词（确定性接地下限）。
            # 关闭 REPORT_SIGNAL_PACK 时不构建，self._signal_pack 保持空串，行为不变。
            if getattr(Config, "REPORT_SIGNAL_PACK", False) and not self._signal_pack:
                try:
                    self._signal_pack = self._build_signal_pack()
                    if self._signal_pack:
                        logger.info(f"已注入模拟量化信号包（{len(self._signal_pack)} 字）到各章节提示词")
                except Exception as _sp_err:  # noqa: BLE001 — 信号包为可选增强，失败不影响主流程
                    logger.warning(f"构建模拟量化信号包失败（忽略）: {_sp_err}")
                    self._signal_pack = ""

            # NEXTSTEPS P0-1: 先于章节叙事推导「预测骨架」（情景+概率+判定标准），把骨架块注入
            # 每章提示词，让叙事对齐并捍卫所分配概率。默认开；关闭或推导失败时为 no-op（回退成稿后抽取）。
            if (getattr(Config, "REPORT_STRUCTURED_FORECAST", True)
                    and getattr(Config, "REPORT_FORECAST_SPINE_FIRST", True)):
                self._derive_and_pin_forecast_spine(report_id)

            total_sections = len(outline.sections)
            generated_sections = []  # 保存内容用于上下文
            failed_section_titles = []  # 记录生成失败（写入占位符）的章节，用于状态汇报

            # EXECPLAN2 I-6-3: optionally pre-generate sections concurrently. Default
            # REPORT_SECTION_CONCURRENCY=1 → _precomputed stays None → the serial loop
            # below calls _generate_section inline exactly as before (byte-identical).
            # REPORT_SECTION_CONTEXT_MODE=brief (serial path) swaps the O(N²) full
            # prior-section context for the compact synthesis brief.
            _precomputed = None
            _context_mode = (getattr(Config, "REPORT_SECTION_CONTEXT_MODE", "full") or "full").strip().lower()
            _section_brief = ""
            try:
                _concurrency = max(1, int(getattr(Config, "REPORT_SECTION_CONCURRENCY", 1) or 1))
            except (TypeError, ValueError):
                _concurrency = 1
            if _concurrency > 1 and total_sections > 1:
                logger.info(f"I-6-3: 并发生成 {total_sections} 个章节（concurrency={_concurrency}）")
                _precomputed = self._generate_sections_concurrent(outline, _concurrency)
            elif _context_mode == "brief":
                _section_brief = self._build_synthesis_brief(outline.sections)

            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # 更新进度
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"正在生成章节: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"正在生成章节: {section.title} ({section_num}/{total_sections})"
                    )
                
                # EXECPLAN2 I-5-4: per-section 遥测——记录本章节开始时刻与累计计量快照、
                # 归零本章节工具计数；章节结束后相减得到该章节的 LLM 经济学。
                # I-6-3: 并发模式下章节生成已发生在线程池中，逐章 meter 差值与 _section_tool_calls
                # 计数器（被多线程共享递增）都不再可归因到单个章节，故跳过逐章遥测（run 级汇总仍准确）。
                _sec_telemetry_on = _telemetry_on and _precomputed is None
                _sec_started = time.monotonic()
                _sec_meter_before = self._meter_total(_telemetry_run_id) if _sec_telemetry_on else {}
                self._section_tool_calls = 0

                # 生成主章节内容。
                # 纵深防御：单个章节的 LLM 调用可能抛异常（如 MiniMax 域内容审核 422
                # new_sensitive、限流、网络错误）。绝不让单章节失败拖垮整份报告——捕获后
                # 写入失败占位符，沿用既有的 failed_section_titles 机制，其余章节照常生成，
                # 最终产出一份"部分完成"的报告而非整体失败。
                try:
                    if _precomputed is not None:
                        # I-6-3: content already produced concurrently; bookkeeping stays serial/in-order.
                        section_content = _precomputed.get(i, SECTION_FAILURE_PLACEHOLDER)
                    else:
                        # I-6-3: brief context mode swaps full prior-section text for the compact brief.
                        _prev_ctx = [_section_brief] if (_context_mode == "brief" and _section_brief) else generated_sections
                        section_content = self._generate_section(
                            section=section,
                            outline=outline,
                            previous_sections=_prev_ctx,
                            progress_callback=lambda stage, prog, msg:
                                progress_callback(
                                    stage,
                                    base_progress + int(prog * 0.7 / total_sections),
                                    msg
                                ) if progress_callback else None,
                            section_index=section_num
                        )
                except Exception as sec_err:  # noqa: BLE001 — 章节级容错，绝不整体失败
                    logger.error(f"章节 LLM 调用异常（已降级为占位符）: {section.title} -> {sec_err}")
                    if self.report_logger:
                        try:
                            self.report_logger.log_error(str(sec_err), stage="generating", section_title=section.title)
                        except Exception:
                            pass
                    section_content = SECTION_FAILURE_PLACEHOLDER

                # EXECPLAN2 I-3-4: 若为情景对比章节且开关开启，把确定性结构化对比表
                # 前置到本章正文，使 LLM 围绕权威差值叙述（不复算/不反转方向）。
                if (
                    self.base_simulation_id
                    and getattr(Config, "REPORT_COMPARISON_TABLE", False)
                    and section_content != SECTION_FAILURE_PLACEHOLDER
                    and self._is_comparison_section(section.title)
                ):
                    try:
                        diff_dict = self._scenario_diff_structured()
                        if diff_dict:
                            table_md = self._render_comparison_table(diff_dict)
                            if table_md:
                                section_content = table_md + "\n\n" + section_content
                                # 落盘结构化对比工件，供 UI / diff 工具消费
                                cpath = os.path.join(
                                    ReportManager._get_report_folder(report_id), "comparison.json"
                                )
                                write_text_atomic(
                                    cpath, json.dumps(diff_dict, ensure_ascii=False, indent=2)
                                )
                                logger.info(f"已注入结构化对比表并写入 comparison.json: {report_id}")
                    except Exception as _ct_err:  # noqa: BLE001 — 对比表为可选增强，失败不影响主流程
                        logger.warning(f"注入结构化对比表失败（忽略）: {_ct_err}")

                section.content = section_content
                if section_content == SECTION_FAILURE_PLACEHOLDER:
                    failed_section_titles.append(section.title)
                    logger.error(f"章节生成失败（已写入占位符）: {section.title}")
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # 保存章节
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # 记录章节完成日志
                full_section_content = f"## {section.title}\n\n{section_content}"

                # EXECPLAN2 I-5-4: 计算本章节遥测条目（LLM 调用/tokens/cost/latency + 工具调用）
                # I-6-3: 并发模式跳过（_sec_telemetry_on 已含 _precomputed is None 判断）。
                _sec_telemetry = None
                if _sec_telemetry_on:
                    try:
                        _sec_after = self._meter_total(_telemetry_run_id)
                        _sec_telemetry = self._meter_delta(
                            _sec_meter_before, _sec_after,
                            duration_s=time.monotonic() - _sec_started,
                            tool_calls=self._section_tool_calls,
                        )
                        _sec_telemetry["section_title"] = section.title
                        _sec_telemetry["section_index"] = section_num
                        section_rollup.append(_sec_telemetry)
                    except Exception:  # noqa: BLE001 — 遥测计算失败不得影响报告生成
                        _sec_telemetry = None

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip(),
                        telemetry=_sec_telemetry
                    )

                logger.info(f"章节已保存: {report_id}/section_{section_num:02d}.md")
                
                # 更新进度
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"章节 {section.title} 已完成",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # 阶段3: 组装完整报告
            if progress_callback:
                progress_callback("generating", 95, "正在组装完整报告...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "正在组装完整报告...",
                completed_sections=completed_section_titles
            )
            
            # 使用ReportManager组装完整报告
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            # 部分完成（partial）：把失败（写入占位符）的章节标题挂到 report，经 to_dict 暴露
            # failed_sections / partial。状态仍为 completed（前端以 status==='completed' 判定终态），
            # 由 partial 标记区分「完整」与「部分完成」。无失败章节时为空列表 → 与历史一致。
            report.failed_sections = list(failed_section_titles)

            # EXECPLAN2 I-3-0/I-9-1/I-3-1 + NEXTSTEPS P0-1/P2-1/P2-3: 最终化结构化预测。
            # 优先采用先于叙事推导的骨架（P0-1），否则从成稿抽取；随后红队自校准（P2-1）+
            # 引用接地审计 + 发布门（P2-3）→ forecast.json。默认开；失败仅告警（degrade-safe）。
            if getattr(Config, "REPORT_STRUCTURED_FORECAST", True):
                try:
                    self._finalize_structured_forecast(report_id, report.markdown_content)
                except Exception as _fe:  # noqa: BLE001
                    logger.warning(f"结构化预测最终化失败（忽略，不影响主报告）: {_fe}")
                # NEXTSTEPS P2-2: 追加确定性「如何验证本预测」章节（判定标准 + 观察指标）。
                if (getattr(Config, "REPORT_RESOLUTION_SECTION", True)
                        and self._forecast_spine and self._forecast_spine.get("scenarios")):
                    try:
                        self._append_resolution_section(report_id, report)
                    except Exception as _rs_err:  # noqa: BLE001
                        logger.warning(f"追加判定标准章节失败（忽略）: {_rs_err}")

            # 报告整体仍标记为 completed（确实跑完了），但若有章节写入了失败占位符，
            # 必须显著告警，避免"假完成"掩盖失败章节（历史上是静默写入污染内容）。
            if failed_section_titles:
                logger.warning(
                    f"报告 {report_id} 完成，但有 {len(failed_section_titles)}/{total_sections} "
                    f"个章节生成失败（已写入占位符）: {failed_section_titles}。"
                    f"这些章节可在修复后单独重试。"
                )
            
            # 计算总耗时
            total_time_seconds = (datetime.now() - start_time).total_seconds()

            # EXECPLAN2 I-5-4: 汇总报告级遥测（per-section rollup + totals），写入完成日志、
            # telemetry.json 工件，并挂到 Report.telemetry 以便经 /report/<id> 与 by-simulation 暴露。
            telemetry_totals = None
            if _telemetry_on:
                try:
                    # 报告级 totals 取 stage='report' 切片并相对基线求差：
                    # 即便 run_id 与上游/其它报告共享，也只计入本报告这次的报告阶段花销。
                    def _diff(after: Dict[str, Any], before: Dict[str, Any], key: str) -> float:
                        try:
                            return float(after.get(key, 0) or 0) - float(before.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            return 0.0
                    snap_after = self._meter_stage_total(_telemetry_run_id, "report")
                    telemetry_totals = {
                        "report_id": report_id,
                        "run_id": _telemetry_run_id,
                        "total_sections": total_sections,
                        "failed_sections": len(failed_section_titles),
                        "duration_s": round(total_time_seconds, 2),
                        "llm_calls": max(0, int(_diff(snap_after, _report_stage_before, "calls"))),
                        "tokens": max(0, int(_diff(snap_after, _report_stage_before, "total_tokens"))),
                        "prompt_tokens": max(0, int(_diff(snap_after, _report_stage_before, "prompt_tokens"))),
                        "completion_tokens": max(0, int(_diff(snap_after, _report_stage_before, "completion_tokens"))),
                        "est_cost_usd": round(max(0.0, _diff(snap_after, _report_stage_before, "cost_usd")), 6),
                        "latency_ms": round(max(0.0, _diff(snap_after, _report_stage_before, "latency_ms")), 1),
                        "tool_calls": sum(int(s.get("tool_calls", 0) or 0) for s in section_rollup),
                    }
                    report.telemetry = {"totals": telemetry_totals, "sections": section_rollup}
                    try:
                        tpath = os.path.join(
                            ReportManager._get_report_folder(report_id), "telemetry.json"
                        )
                        write_text_atomic(
                            tpath, json.dumps(report.telemetry, ensure_ascii=False, indent=2)
                        )
                    except Exception as _twe:  # noqa: BLE001 — 工件落盘失败不影响主流程
                        logger.warning(f"telemetry.json 写入失败（忽略）: {_twe}")
                    logger.info(
                        f"报告遥测: {report_id} 共 {telemetry_totals['llm_calls']} 次 LLM 调用, "
                        f"{telemetry_totals['tokens']} tokens, "
                        f"~${telemetry_totals['est_cost_usd']:.4f}, "
                        f"{telemetry_totals['duration_s']}s, {total_sections} 章"
                    )
                except Exception as _te:  # noqa: BLE001 — 遥测汇总失败不得影响报告完成
                    logger.warning(f"报告级遥测汇总失败（忽略）: {_te}")
                    telemetry_totals = None

            # 记录报告完成日志
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds,
                    section_rollup=(section_rollup if _telemetry_on else None),
                    totals=telemetry_totals,
                )

            # 保存最终报告
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "报告生成完成",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "报告生成完成")
            
            logger.info(f"报告生成完成: {report_id}")
            
            # 关闭控制台日志记录器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"报告生成失败: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # 记录错误日志
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # 保存失败状态
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"报告生成失败: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # 忽略保存失败的错误
            
            # 关闭控制台日志记录器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None

            return report

        finally:
            # EXECPLAN2 I-5-4: 还原进入本方法前的 LLM 计量上下文，避免污染复用线程/调用方。
            if _telemetry_on and _prev_run_ctx is not None:
                try:
                    set_run_context(_prev_run_ctx[0], _prev_run_ctx[1])
                except Exception:  # noqa: BLE001 — 还原失败仅影响后续计量归属，不影响报告
                    pass

    def _resolve_report_cached(self) -> Optional[Report]:
        """EXECPLAN2 F-7-3: 解析本 simulation 的最新报告并在实例级记忆，避免重复扫描。"""
        if self._cached_report is not self._cached_report_sentinel:
            return self._cached_report
        report = ReportManager.get_report_by_simulation(self.simulation_id)
        self._cached_report = report
        return report

    def chat(
        self,
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        与Report Agent对话
        
        在对话中Agent可以自主调用检索工具来回答问题
        
        Args:
            message: 用户消息
            chat_history: 对话历史
            
        Returns:
            {
                "response": "Agent回复",
                "tool_calls": [调用的工具列表],
                "sources": [信息来源]
            }
        """
        logger.info(f"Report Agent对话: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # 获取已生成的报告内容
        report_content = ""
        try:
            report = self._resolve_report_cached()  # EXECPLAN2 F-7-3: 实例级记忆 + 索引快路径
            if report and report.markdown_content:
                # 限制报告长度，避免上下文过长（放宽到 40000 字以覆盖更长的报告）
                report_content = report.markdown_content[:40000]
                if len(report.markdown_content) > 40000:
                    report_content += "\n\n... [报告内容已截断] ..."
        except Exception as e:
            logger.warning(f"获取报告内容失败: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "（暂无报告）",
            tools_description=self._get_tools_description(),
        )

        # 构建消息
        messages = [{"role": "system", "content": system_prompt}]
        
        # 添加历史对话
        for h in chat_history[-10:]:  # 限制历史长度
            messages.append(h)
        
        # 添加用户消息
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT循环（简化版）
        tool_calls_made = []
        max_iterations = 2  # 减少迭代轮数
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # 解析工具调用
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # 没有工具调用，直接返回响应
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # 执行工具调用（限制数量）
            tool_results = []
            for call in tool_calls[:1]:  # 每轮最多执行1次工具调用
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # 限制结果长度
                })
                tool_calls_made.append(call)
            
            # 将结果添加到消息
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[{r['tool']}结果]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # 达到最大迭代，获取最终响应
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # 清理响应
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    报告管理器
    
    负责报告的持久化存储和检索
    
    文件结构（分章节输出）：
    reports/
      {report_id}/
        meta.json          - 报告元信息和状态
        outline.json       - 报告大纲
        progress.json      - 生成进度
        section_01.md      - 第1章节
        section_02.md      - 第2章节
        ...
        full_report.md     - 完整报告
    """
    
    # 报告存储目录
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')

    # EXECPLAN2 F-7-3: simulation_id -> [report_id, ...] 轻量索引文件，
    # 让 get_report_by_simulation 免去对全部报告文件夹的 O(N) 全量扫描 + 全文反序列化。
    _SIM_INDEX_FILENAME = "_sim_index.json"
    # 串行化索引文件的读改写（同进程内）
    _sim_index_lock = threading.Lock()

    @classmethod
    def _ensure_reports_dir(cls):
        """确保报告根目录存在"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """获取报告文件夹路径"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """确保报告文件夹存在并返回路径"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """获取报告元信息文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """获取完整报告Markdown文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")

    # ── EXECPLAN2 F-7-3: simulation_id 轻量索引 + 仅读 meta 头部 ──

    @classmethod
    def _get_sim_index_path(cls) -> str:
        """获取 simulation_id 索引文件路径"""
        return os.path.join(cls.REPORTS_DIR, cls._SIM_INDEX_FILENAME)

    @classmethod
    def _read_report_meta(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """仅读取并解析某报告的 meta.json（不重建 Report、不读取 full_report.md）。

        meta.json 内嵌了完整 markdown，json.load 仍会读全文；该方法主要用于在已知
        候选 report_id 时只解析一次，避免遍历全部文件夹。读取失败返回 None。
        """
        path = cls._get_report_path(report_id)
        if not os.path.exists(path):
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if not os.path.exists(old_path):
                return None
            path = old_path
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    @classmethod
    def _load_sim_index(cls) -> Dict[str, List[str]]:
        """读取 simulation_id -> [report_id, ...] 索引；缺失/损坏时返回空字典。"""
        path = cls._get_sim_index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 规范化为 {sim_id: [report_id,...]}
                norm: Dict[str, List[str]] = {}
                for k, v in data.items():
                    if isinstance(v, list):
                        norm[k] = [str(x) for x in v]
                    elif v:
                        norm[k] = [str(v)]
                return norm
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    @classmethod
    def _index_add(cls, simulation_id: str, report_id: str) -> None:
        """把 (simulation_id, report_id) 写入索引（原子写、加锁，幂等）。EXECPLAN2 F-7-3"""
        if not simulation_id or not report_id:
            return
        cls._ensure_reports_dir()
        with cls._sim_index_lock:
            index = cls._load_sim_index()
            ids = index.get(simulation_id, [])
            if report_id not in ids:
                ids.append(report_id)
            index[simulation_id] = ids
            try:
                write_json_atomic(cls._get_sim_index_path(), index, indent=2)
            except Exception as e:  # noqa: BLE001 — 索引仅为优化，失败不应阻断保存
                logger.warning(f"更新 simulation 索引失败（忽略，回退全量扫描）: {e}")

    @classmethod
    def _index_remove(cls, report_id: str) -> None:
        """从索引中移除某 report_id（删除报告时调用）。EXECPLAN2 F-7-3"""
        if not report_id:
            return
        with cls._sim_index_lock:
            index = cls._load_sim_index()
            changed = False
            for sim_id in list(index.keys()):
                if report_id in index[sim_id]:
                    index[sim_id] = [r for r in index[sim_id] if r != report_id]
                    changed = True
                    if not index[sim_id]:
                        del index[sim_id]
            if changed:
                try:
                    write_json_atomic(cls._get_sim_index_path(), index, indent=2)
                except Exception as e:  # noqa: BLE001 — 索引仅为优化，失败不阻断删除
                    logger.warning(f"清理 simulation 索引失败（忽略）: {e}")

    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """获取大纲文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """获取进度文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """获取章节Markdown文件路径"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """获取 Agent 日志文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """获取控制台日志文件路径"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        获取控制台日志内容
        
        这是报告生成过程中的控制台输出日志（INFO、WARNING等），
        与 agent_log.jsonl 的结构化日志不同。
        
        Args:
            report_id: 报告ID
            from_line: 从第几行开始读取（用于增量获取，0 表示从头开始）
            
        Returns:
            {
                "logs": [日志行列表],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": 是否还有更多日志
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # 保留原始日志行，去掉末尾换行符
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        获取完整的控制台日志（一次性获取全部）
        
        Args:
            report_id: 报告ID
            
        Returns:
            日志行列表
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        获取 Agent 日志内容
        
        Args:
            report_id: 报告ID
            from_line: 从第几行开始读取（用于增量获取，0 表示从头开始）
            
        Returns:
            {
                "logs": [日志条目列表],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": 是否还有更多日志
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # 跳过解析失败的行
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        获取完整的 Agent 日志（用于一次性获取全部）
        
        Args:
            report_id: 报告ID
            
        Returns:
            日志条目列表
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        保存报告大纲
        
        在规划阶段完成后立即调用
        """
        cls._ensure_report_folder(report_id)
        
        # 原子写入，避免轮询端点读到半截 JSON（EXECPLAN2 F-7-6）
        write_text_atomic(cls._get_outline_path(report_id),
                          json.dumps(outline.to_dict(), ensure_ascii=False, indent=2))

        logger.info(f"大纲已保存: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        保存单个章节

        在每个章节生成完成后立即调用，实现分章节输出

        Args:
            report_id: 报告ID
            section_index: 章节索引（从1开始）
            section: 章节对象

        Returns:
            保存的文件路径
        """
        cls._ensure_report_folder(report_id)

        # 构建章节Markdown内容 - 清理可能存在的重复标题
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # 保存文件
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        write_text_atomic(file_path, md_content)  # 原子写入（F-7-6）

        logger.info(f"章节已保存: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        清理章节内容
        
        1. 移除内容开头与章节标题重复的Markdown标题行
        2. 将所有 ### 及以下级别的标题转换为粗体文本
        
        Args:
            content: 原始内容
            section_title: 章节标题
            
        Returns:
            清理后的内容
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 检查是否是Markdown标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # 检查是否是与章节标题重复的标题（跳过前5行内的重复）
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # 将所有级别的标题（#, ##, ###, ####等）转换为粗体
                # 因为章节标题由系统添加，内容中不应有任何标题
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # 添加空行
                continue
            
            # 如果上一行是被跳过的标题，且当前行为空，也跳过
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # 移除开头的空行
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # 移除开头的分隔线
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # 同时移除分隔线后的空行
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        更新报告生成进度
        
        前端可以通过读取progress.json获取实时进度
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        write_text_atomic(cls._get_progress_path(report_id),
                          json.dumps(progress_data, ensure_ascii=False, indent=2))  # 原子写入（F-7-6）

    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """获取报告生成进度"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        获取已生成的章节列表
        
        返回所有已保存的章节文件信息
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 从文件名解析章节索引
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        组装完整报告
        
        从已保存的章节文件组装完整报告，并进行标题清理
        """
        folder = cls._get_report_folder(report_id)
        
        # 构建报告头部
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # 按顺序读取所有章节文件
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # 后处理：清理整个报告的标题问题
        md_content = cls._post_process_report(md_content, outline)
        
        # 保存完整报告（原子写入，F-7-6）
        full_path = cls._get_report_markdown_path(report_id)
        write_text_atomic(full_path, md_content)

        logger.info(f"完整报告已组装: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        后处理报告内容
        
        1. 移除重复的标题
        2. 保留报告主标题(#)和章节标题(##)，移除其他级别的标题(###, ####等)
        3. 清理多余的空行和分隔线
        
        Args:
            content: 原始报告内容
            outline: 报告大纲
            
        Returns:
            处理后的内容
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # 收集大纲中的所有章节标题
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 检查是否是标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # 检查是否是重复标题（在连续5行内出现相同内容的标题）
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # 跳过重复标题及其后的空行
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # 标题层级处理：
                # - # (level=1) 只保留报告主标题
                # - ## (level=2) 保留章节标题
                # - ### 及以下 (level>=3) 转换为粗体文本
                
                if level == 1:
                    if title == outline.title:
                        # 保留报告主标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # 章节标题错误使用了#，修正为##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # 其他一级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # 保留章节标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # 非章节的二级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### 及以下级别的标题转换为粗体文本
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # 跳过标题后紧跟的分隔线
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # 标题后只保留一个空行
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # 清理连续的多个空行（保留最多2个）
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """保存报告元信息和完整报告"""
        cls._ensure_report_folder(report.report_id)
        
        # 保存元信息JSON（原子写入，F-7-6）
        write_text_atomic(cls._get_report_path(report.report_id),
                          json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

        # 保存大纲
        if report.outline:
            cls.save_outline(report.report_id, report.outline)

        # 保存完整Markdown报告（原子写入）
        if report.markdown_content:
            write_text_atomic(cls._get_report_markdown_path(report.report_id), report.markdown_content)

        # EXECPLAN2 F-7-3: 维护 simulation_id -> report_id 轻量索引，加速 by-simulation 查询
        cls._index_add(report.simulation_id, report.report_id)

        logger.info(f"报告已保存: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """获取报告"""
        # EXECPLAN2 F-7-3: 保留文件名（索引）不是报告，直接忽略，避免误解析
        if f"{report_id}.json" == cls._SIM_INDEX_FILENAME or report_id == cls._SIM_INDEX_FILENAME[:-5]:
            return None

        path = cls._get_report_path(report_id)

        if not os.path.exists(path):
            # 兼容旧格式：检查直接存储在reports目录下的文件
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        # EXECPLAN2 F-7-3: 非报告结构（如索引/旧版无关 JSON）容错，返回 None 而非抛错
        if not isinstance(data, dict) or 'report_id' not in data or 'simulation_id' not in data:
            return None

        # 重建Report对象
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', ''),
                    description=s.get('description', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # 如果markdown_content为空，尝试从full_report.md读取
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error'),
            # 部分完成信息：从 meta.json 还原失败章节标题列表（旧报告无此键 → 空列表，partial=False）
            failed_sections=data.get('failed_sections') or [],
            telemetry=data.get('telemetry')  # EXECPLAN2 I-5-4: 从 meta.json 还原紧凑遥测，经 API 暴露
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """根据模拟ID获取报告（EXECPLAN2 F-7-0 / F-7-3）。

        旧实现遍历 os.listdir 返回首个匹配项——listdir 顺序由文件系统决定，
        force_regenerate 留下多份同 simulation 报告时返回的往往是过期报告且不确定。
        新实现：
          1) 先查 simulation_id 轻量索引（F-7-3），仅解析候选 report_id 的 meta，
             避免对全部报告文件夹做 O(N) 全量扫描；
          2) 在候选中按 created_at 取最新（确定性，对齐 list_reports 的排序，F-7-0 A）；
          3) 索引缺失/未命中时回退全量扫描，同样按 created_at 取最新而非首个 listdir 命中。
        """
        cls._ensure_reports_dir()

        # ① 索引快路径：候选 report_id -> (created_at, report_id)，仅读 meta 头部
        index = cls._load_sim_index()
        candidates: List[tuple] = []  # (created_at, report_id)
        for rid in index.get(simulation_id, []):
            meta = cls._read_report_meta(rid)
            if meta and meta.get('simulation_id') == simulation_id:
                candidates.append((meta.get('created_at', ''), rid))
        if candidates:
            best_rid = max(candidates, key=lambda x: x[0])[1]
            report = cls.get_report(best_rid)
            if report and report.simulation_id == simulation_id:
                return report

        # ② 回退：全量扫描，确定性地按 created_at 取最新（F-7-0 A）
        matches: List[Report] = []
        for item in os.listdir(cls.REPORTS_DIR):
            if item == cls._SIM_INDEX_FILENAME:  # EXECPLAN2 F-7-3: 跳过索引文件，避免误当报告解析
                continue
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新格式：文件夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    matches.append(report)
            # 兼容旧格式：JSON文件
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    matches.append(report)

        if not matches:
            return None
        return max(matches, key=lambda r: r.created_at)

    @classmethod
    def delete_other_reports_for_simulation(cls, simulation_id: str, keep_report_id: str) -> int:
        """EXECPLAN2 F-7-0 (B 的 in-file 部分): 删除某 simulation 除 keep_report_id 外的所有报告，
        修复 force_regenerate 留下的孤儿文件夹泄漏与过期短路。

        仅在新报告已成功落盘后调用，确保该 simulation 不会被清成零报告。
        调用方（api/report.py 等，跨文件不在本次改动范围）应在新报告 COMPLETED 后调用本方法。
        返回删除的报告数量。
        """
        deleted = 0
        for report in cls.list_reports(simulation_id=simulation_id, limit=10_000):
            if report.report_id != keep_report_id:
                try:
                    if cls.delete_report(report.report_id):
                        deleted += 1
                except Exception as e:  # noqa: BLE001 — 清理失败不应影响主流程
                    logger.warning(f"清理同 simulation 旧报告失败（忽略）: {report.report_id}: {e}")
        return deleted

    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """列出报告"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            if item == cls._SIM_INDEX_FILENAME:  # EXECPLAN2 F-7-3: 跳过索引文件，避免误当报告解析
                continue
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新格式：文件夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # 兼容旧格式：JSON文件
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # 按创建时间倒序
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """删除报告（整个文件夹）"""
        import shutil

        folder_path = cls._get_report_folder(report_id)

        # 新格式：删除整个文件夹
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            cls._index_remove(report_id)  # EXECPLAN2 F-7-3: 同步清理索引
            logger.info(f"报告文件夹已删除: {report_id}")
            return True

        # 兼容旧格式：删除单独的文件
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")

        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True

        if deleted:
            cls._index_remove(report_id)  # EXECPLAN2 F-7-3: 同步清理索引

        return deleted
