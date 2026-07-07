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
from typing import Dict, Any, List, Optional, Callable, Tuple
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
    # BILINGUAL：自动生成的另一语种版本清单。每条形如
    # {lang, source_lang, path, chars, created_at, model, translation_quality, missing_numbers}。
    # 仅在 REPORT_BILINGUAL 开启且成功翻译时填充；否则为 None，to_dict 输出与历史完全一致
    # （向后兼容，旧 reader 不受影响）。
    translations: Optional[List[Dict[str, Any]]] = None

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
        # BILINGUAL：仅在有翻译版本时附加 translations 键（向后兼容，旧报告无此键）
        if self.translations is not None:
            d["translations"] = self.translations
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
- 最少6个章节，最多14个章节
- 每个章节直接撰写完整内容（每章为一个深入的长篇分析），章节内部可用 ### 三级小标题分子小节
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

注意：sections数组最少6个，最多14个元素！每个章节都应是一篇深入详实的长篇分析。"""

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

【再次提醒】报告章节数量：最少6个，最多14个；每章都是一篇深入详实的长篇分析，全面覆盖核心预测发现的不同维度。"""

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
   - 每个章节至少调用{min_tool_calls}次工具（最多{max_tool_calls}次）来观察模拟的世界，它代表了未来

2. 【引用Agent言行——必须标注为「模拟推演」，严禁伪装成真实信源】
   - 模拟中 Agent 的发言/行为是对未来人群行为的**推演**，不是真实世界已发生的事实
   - 引用时必须显式标注来源为模拟代理人，例如：
     > 模拟代理人「<角色名>」推演：原文内容...
   - ❌ 严禁把模拟 Agent 的发言伪装成真实人物/真实采访/分析师/媒体的话
     （禁止「某分析师在采访中表示」「据<真实媒体>报道」之类把模拟内容嫁接到真实信源）
   - ❌ 严禁把知识图谱里的关系描述、或系统自己推导的概率，当作某人/某机构的「原话」引用
   - 真实世界的事实/数据/观点只能来自研究材料（用 [S] 引用索引标注），不得与模拟推演混为一谈

3. 【语言一致性 - 引用内容必须翻译为报告语言】
   - 工具返回的内容可能包含英文或中英文混杂的表述
   - 如果模拟需求和材料原文是中文的，报告必须全部使用中文撰写
   - 当你引用工具返回的英文或中英混杂内容时，必须将其翻译为流畅的中文后再写入报告
   - 翻译时保持原意不变，确保表述自然通顺
   - 这一规则同时适用于正文和引用块（> 格式）中的内容

4. 【忠实呈现 —— 反捏造纪律（最高优先级）】
   - 报告内容必须反映模拟推演结果与研究材料，且二者来源分明
   - ❌ 严禁编造研究材料/模拟中不存在的数字、引文、来源、URL、日期或事件
   - 每个**承重数字**：要么能在研究材料中找到并标 [S]，要么明确标注为「模拟推演所得」
   - 若模拟为空洞/未产生有机互动（系统会在 simulation_health 标注），**不得**虚构 Agent 言行或「共识」，
     转而基于研究证据做因果推理，并显式说明「本轮模拟信号有限」
   - 信息不足时如实说明，绝不用流畅叙事填补证据空白

5. 【写作质量 —— 拒绝「通用 LLM 腔」(评审一眼判死的就是这个)】
   - 不要重复前文章节已说过的告诫/结论；读 previous_content，本章必须推进一个**新论点**
   - 每个承重段落给出**机制**（A 导致 B、B 又迫使 C），而非笼统断言或形容词堆砌
   - 至少呈现一处**真实分歧**：steelman 一个相反观点再回应它；禁止「众口一词」式叙述
   - 控制破折号/「不仅…而且」「值得注意的是」等填充套话；用具体数字与实例代替修辞
   - 允许自然的散文流，不必每段都套**粗体小标题**；像一个有观点的人类分析师那样写

═══════════════════════════════════════════════════════════════
【⚠️ 格式规范 - 极其重要！】
═══════════════════════════════════════════════════════════════

【一个章节 = 最小内容单位；用 ### 三级小标题组织内部结构】
- 每个章节是报告的最小分块单位；章节主标题（## 级）由系统自动添加，你只需撰写正文
- ❌ 禁止在章节内使用一级/二级标题（# 或 ##）——那是报告主标题/章节标题的层级，会破坏结构
- ❌ 禁止在内容开头重复本章标题
- ✅ **鼓励**在章节内用 2-4 个「### 三级小标题」把长正文切成清晰的子小节（提升可读性与结构密度）
- ✅ 也可辅以**粗体**、段落分隔、引用、列表；但优先用 ### 组织主要子小节
- ❌ 不要用 #### 及更深层级（四级及以下），保持 ### 单层子小节纪律

【正确示例】
```
本章节分析了事件的舆论传播态势。通过对模拟数据的深入分析，我们发现...

### 首发引爆阶段

微博作为舆情的第一现场，承担了信息首发的核心功能：

> "微博贡献了68%的首发声量..."

### 情绪放大阶段

抖音平台进一步放大了事件影响力：

- 视觉冲击力强
- 情绪共鸣度高
```

【错误示例】
```
## 执行摘要          ← 错误！## 是章节标题层级，不要在正文里用
# 一、首发阶段        ← 错误！# 是报告主标题层级
#### 1.1 详细分析     ← 错误！不要用 #### 及更深层级，最多到 ###

本章节分析了...
```

═══════════════════════════════════════════════════════════════
【可用检索工具】（每章节调用{min_tool_calls}-{max_tool_calls}次）
═══════════════════════════════════════════════════════════════

{tools_description}

【工具使用建议 - 请混合使用不同工具，不要只用一种】
{tool_usage_hints}

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
   - 正文长度不少于 {section_floor_chars} 字，目标 {section_target_lo}–{section_target_hi} 字（不含引用块）
   - 用 2-4 个「### 三级小标题」把这篇长正文切成清晰的子小节，每个子小节围绕一个论点展开
   - 必须层层展开：先给出整体判断，再分多个角度深入论证，每个角度都要有
     具体的模拟证据（数据、事件、Agent 原话）支撑
   - 充分展开因果链条、二阶效应、不同人群的分化反应、潜在转折点
   - ❌ 严禁写成几百字的提纲式摘要或泛泛而谈——那是不合格的章节
   - ✅ 像撰写一篇严肃深度报告的章节那样，写得充实、有洞察、有层次
1. 内容必须基于工具检索到的模拟数据
2. 大量引用原文来展示模拟效果
3. 使用Markdown格式：
   - ✅ 用「### 三级小标题」组织 2-4 个子小节（章节内部结构）
   - 使用 **粗体文字** 标记子小节内的重点
   - 使用列表（-或1.2.3.）组织要点
   - 使用空行分隔不同段落
   - ❌ 禁止使用 # 或 ##（报告主标题/章节标题层级），也不要用 #### 及更深层级
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
7. 【再次强调】用 2-4 个「### 三级小标题」组织本章子小节；禁止 # 或 ##，也不要 #### 及更深层级"""

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
- ✅ 用 2-4 个「### 三级小标题」组织本章子小节（章节内部结构）
- ❌ 不要用 # 或 ##（报告主标题/章节标题层级），也不要用 #### 及更深层级
- ❌ 不要写"{section_title}"作为开头（章节标题由系统自动添加）

请开始：
1. 首先思考（Thought）这个章节需要什么信息
2. 然后调用工具（Action）获取模拟数据（建议 {min_tool_calls}-{max_tool_calls} 次，覆盖多个角度）
3. 收集足够信息后输出 Final Answer（正文用 ### 三级小标题分节，不要用 # 或 ##）
4. 【篇幅】Final Answer 必须充实详尽，不少于 {section_floor_chars} 字、目标 {section_target_lo}–{section_target_hi} 字，层层深入、证据扎实，不要写成简短摘要"""

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
# RQ-1：200→800——展开后的章节目标 3000-6000 字，几百字的残段应判为未真正撰写。
# 但含图表标记（一张 Mermaid/内嵌图 + 简短图注）的短章节是合法产出，见 _looks_contaminated
# 的豁免（与 pipeline_orchestrator 健康门的 ('![','<img','<svg','```mermaid') 豁免同源）。
MIN_VALID_SECTION_CHARS = 800

# RQ-1：含以下任一标记的短章节是合法图表产出，豁免 <MIN_VALID_SECTION_CHARS 的长度门。
# 必须与 pipeline_orchestrator._section_health 的豁免集逐字一致，避免两侧判定漂移。
_FIGURE_MARKUP_MARKERS = ("![", "<img", "<svg", "```mermaid")


def derive_report_shape(
    page_budget: Optional[int] = None,
    *,
    expanded_min_sections: int = 6,
    expanded_max_sections: int = 14,
    expanded_floor_chars: int = 2000,
    expanded_target_lo: int = 3000,
    expanded_target_hi: int = 6000,
    expanded_tool_budget: int = 12,
    compact_max_pages: int = 8,
) -> Dict[str, int]:
    """RQ-1(4)：从需求书 page_budget 推导报告「形状」——章节数区间 / 每章字数（下限+目标区间）/
    每章工具预算。纯函数（不读 Config、无副作用），便于单测。

    语义：小 page_budget（<=compact_max_pages 页）保持今天的紧凑形状（5-8 节 / 1500 下限 /
    1800-2800 目标 / 8 次工具）；无 page_budget 或大 page_budget 用展开默认（默认 6-14 节 /
    2000 下限 / 3000-6000 目标 / 12 次工具，由调用方从 Config 注入 expanded_* 覆盖）。

    展开默认取自 Config（REPORT_SECTION_FLOOR_CHARS / report_section_target_chars() /
    REPORT_AGENT_MAX_TOOL_CALLS），由调用方传入以保持本函数纯净。page_budget 非法/<=0 → 视作
    「无预算」走展开（degrade-safe）。返回 dict：min_sections/max_sections/floor_chars/
    target_lo/target_hi/tool_budget。"""
    compact = {
        "min_sections": 5,
        "max_sections": 8,
        "floor_chars": 1500,
        "target_lo": 1800,
        "target_hi": 2800,
        "tool_budget": 8,
    }
    expanded = {
        "min_sections": int(expanded_min_sections),
        "max_sections": int(expanded_max_sections),
        "floor_chars": int(expanded_floor_chars),
        "target_lo": int(expanded_target_lo),
        "target_hi": int(expanded_target_hi),
        "tool_budget": int(expanded_tool_budget),
    }
    if page_budget is None:
        return dict(expanded)
    try:
        pb = int(page_budget)
    except (TypeError, ValueError):
        return dict(expanded)
    if pb <= 0:
        return dict(expanded)
    if pb <= int(compact_max_pages):
        return dict(compact)
    return dict(expanded)

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
    # RQ-1：短于下限判无效，但含图表标记（Mermaid/内嵌图 + 简短图注）的短章节是合法产出，
    # 豁免长度门（与 pipeline_orchestrator 健康门的图表豁免同源，避免两侧判定漂移）。
    if len(text.strip()) < MIN_VALID_SECTION_CHARS:
        if not any(m in text for m in _FIGURE_MARKUP_MARKERS):
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
# PM-2: 「Market Cross-Check」渲染块（预测 vs 市场隐含概率对照 + >10pp 判定 + 未匹配市场）
# ═══════════════════════════════════════════════════════════════
# 幂等判定 + 定位用标记（与渲染的标题一致，供 _prepend_binary_forecasts_section 的幂等门复用）。
_MARKET_XCHECK_MARKERS = ("### Market Cross-Check", "### 市场交叉核对")


def _mc_float(v: Any) -> Optional[float]:
    """把可能是字符串的数值安全转 float；失败/NaN 返回 None（渲染块本地小工具，degrade-safe）。"""
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _mc_cell(x: Any) -> str:
    """markdown 表格单元转义（管道符/换行），与 forecast_extractor / prediction_markets 同风格。"""
    return str(x).replace("|", "／").replace("\n", " ").strip()


def _mc_comparisons_from_forecast(forecast: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 forecast 汇出对照行：优先 forecast['market_comparison']['comparisons']（PM-2 抽取器
    已算好的确定性负载），缺失时从 binary_forecasts[].market_anchor 现场推导（同字段口径）。

    统一为渲染用 schema：{forecast_id, statement, model_probability, market_id, market_question,
    market_implied_yes_prob, divergence, exceeds_10pp, rationale_cites_market, url}。
    纯函数、无副作用；无可对照数据 → []。"""
    mc = forecast.get("market_comparison")
    if isinstance(mc, dict) and isinstance(mc.get("comparisons"), list):
        rows: List[Dict[str, Any]] = []
        for c in mc["comparisons"]:
            if isinstance(c, dict) and c.get("market_id"):
                rows.append(c)
        if rows:
            return rows
    # 回退：从 binary_forecasts 的 market_anchor 现场推导。
    out: List[Dict[str, Any]] = []
    for b in (forecast.get("binary_forecasts") or []):
        if not isinstance(b, dict):
            continue
        anchor = b.get("market_anchor")
        if not isinstance(anchor, dict) or not anchor.get("market_id"):
            continue
        p = _mc_float(b.get("probability"))
        dv = _mc_float(anchor.get("divergence"))
        out.append({
            "forecast_id": b.get("id"),
            "statement": b.get("statement"),
            "model_probability": p,
            "market_id": anchor.get("market_id"),
            "market_question": anchor.get("question"),
            "market_implied_yes_prob": _mc_float(anchor.get("implied_yes_prob")),
            "divergence": dv,
            "exceeds_10pp": (abs(dv) > 0.10) if dv is not None else False,
            "rationale_cites_market": None,  # 无对照负载时无法判定，留空（渲染按未知处理）
            "url": anchor.get("url"),
        })
    return out


def render_market_comparison_block(forecast: Optional[Dict[str, Any]],
                                   markets: Optional[List[Dict[str, Any]]] = None,
                                   lang: str = "en") -> str:
    """PM-2：渲染确定性「Market Cross-Check」块——预测 vs 市场隐含概率对照 + 未匹配市场清单。

    纯函数（无 LLM/无网络）：
      * 对照表按 |Δ|（分歧绝对值）降序，列出预测概率、市场隐含 P(yes)、Δ（分歧，pp）、
        >10pp 判定（超阈且理由未引用市场 → 需解释；超阈且已引用 → 已解释；带内 → OK）、
        市场链接（有 url 时渲染为可点链接）；
      * 未匹配市场：markets 快照中未被任何预测锚定的市场（按成交量降序），提示尚未接入的信号。
    数据源：forecast['market_comparison'].comparisons（PM-2 抽取器负载）或 binary_forecasts[]
    .market_anchor 现场推导。无对照且无未匹配市场 → ""（调用方跳过，绝不写空块）。双语表头随 lang。"""
    if not isinstance(forecast, dict):
        return ""
    comps = _mc_comparisons_from_forecast(forecast)
    # 未匹配市场 = 快照中 market_id 未出现在任一对照行的市场（按成交量降序，稳定）。
    anchored_ids = {str(c.get("market_id") or "").strip() for c in comps if c.get("market_id")}
    snapshot = [m for m in (markets or []) if isinstance(m, dict)]
    unmatched = [m for m in snapshot
                 if str(m.get("market_id") or "").strip()
                 and str(m.get("market_id")).strip() not in anchored_ids]
    unmatched.sort(key=lambda m: -(_mc_float(m.get("volume")) or 0.0))
    if not comps and not unmatched:
        return ""  # 无任何可对照/未匹配信息 → 不写块
    # 语言判定与 render_binary_forecasts_block 同口径：仅 "en"/"English" 前缀视作英文，
    # 其余（"zh"/"Chinese"/"中文" 等）走中文，兼容短码与语言全名两种传入。
    zh = not str(lang or "").lower().startswith("en")
    if zh:
        lines = ["### 市场交叉核对", "",
                 "_预测概率与真实预测市场隐含概率的确定性对照。市场是校准锚点，非真值；"
                 "分歧超 10 个百分点且理由未引用市场者标注「需解释」。_", ""]
    else:
        lines = ["### Market Cross-Check", "",
                 "_Deterministic cross-check of forecast probabilities against live "
                 "prediction-market implied probabilities. Markets are calibration anchors, "
                 "not ground truth; divergences over 10 percentage points whose rationale does "
                 "not cite the market are flagged for explanation._", ""]
    if comps:
        comps_sorted = sorted(
            comps, key=lambda c: -(abs(_mc_float(c.get("divergence")) or 0.0)))
        if zh:
            headers = ["#", "预测", "预测 P", "市场 P(yes)", "Δ（pp）", ">10pp 判定", "市场"]
        else:
            headers = ["#", "Forecast", "Model P", "Market P(yes)", "Δ (pp)",
                       ">10pp verdict", "Market"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for c in comps_sorted:
            fid = _mc_cell(c.get("forecast_id") or "")
            stmt = _mc_cell(str(c.get("statement") or "")[:120])
            mp = _mc_float(c.get("model_probability"))
            ip = _mc_float(c.get("market_implied_yes_prob"))
            dv = _mc_float(c.get("divergence"))
            mp_s = f"{mp * 100:.0f}%" if mp is not None else "—"
            ip_s = f"{ip * 100:.0f}%" if ip is not None else "—"
            dv_s = f"{dv * 100:+.0f}pt" if dv is not None else "—"
            exceeds = bool(c.get("exceeds_10pp")) or (dv is not None and abs(dv) > 0.10)
            cites = c.get("rationale_cites_market")
            if not exceeds:
                verdict = "带内" if zh else "within band"
            elif cites is True:
                verdict = "已解释" if zh else "explained"
            elif cites is False:
                verdict = "⚠ 需解释" if zh else "⚠ explain"
            else:  # 无对照负载时理由引用未知 → 提示核对
                verdict = "⚠ 待核对" if zh else "⚠ review"
            q = _mc_cell(str(c.get("market_question") or "")[:80])
            url = str(c.get("url") or "").strip()
            market_cell = f"[{q}]({_mc_cell(url)})" if (q and url) else (q or "—")
            lines.append("| " + " | ".join(
                [fid, stmt, mp_s, ip_s, dv_s, verdict, market_cell]) + " |")
    if unmatched:
        lines.append("")
        if zh:
            lines.append("**未匹配市场（快照中未被任何预测锚定，可补充对照）：**")
        else:
            lines.append("**Unmatched markets (in snapshot, not anchored by any "
                         "forecast — candidate cross-checks):**")
        for m in unmatched[:20]:
            ip = _mc_float(m.get("implied_yes_prob"))
            ip_s = f"{ip * 100:.0f}%" if ip is not None else "—"
            q = _mc_cell(str(m.get("question") or "")[:120])
            url = str(m.get("url") or "").strip()
            label = f"[{q}]({_mc_cell(url)})" if (q and url) else (q or _mc_cell(m.get("market_id") or ""))
            if zh:
                lines.append(f"- {label} — 隐含 P(yes) {ip_s}")
            else:
                lines.append(f"- {label} — implied P(yes) {ip_s}")
    return "\n".join(lines)


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

    # 大纲章节数契约（RQ-1：展开默认 6-14 节；PLAN_SYSTEM_PROMPT 同步要求 6-14 节）。此处为
    # 「无/大 page_budget」时的默认钳制边界；小 page_budget 报告经 _report_shape() 收敛回 5-8。
    # 成功路径与 except 兜底均以 _report_shape() 的 min/max 钳制，确保任何路径产出的大纲都落在区间内。
    OUTLINE_MIN_SECTIONS = 6
    OUTLINE_MAX_SECTIONS = 14
    # 兜底章节标题：数量需 >= 任意形状的 min_sections（展开 6），padding 时按需取用去重。
    _FALLBACK_SECTION_TITLES = [
        "预测场景与核心发现",
        "关键行为者与系统动力",
        "模拟证据与行为轨迹",
        "趋势展望与情景推演",
        "风险信号与决策启示",
        "关键转折与不确定性",
        "校准与信心评估",
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
        charts_manifest: Optional[Any] = None,
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
        # QUALITY-OPT F0/B0: decide the report's output language from the brief + research
        # report (an English brief → English submission; a wrong-language report is an automatic
        # round-one fail). Overridable via REPORT_OUTPUT_LANGUAGE. Consumed by _lang_override()
        # (section/plan prompts) and the binary/Part-1 renderers.
        _forced_lang = (os.environ.get("REPORT_OUTPUT_LANGUAGE", "") or "").strip()
        if _forced_lang:
            self.output_language = _forced_lang
        else:
            try:
                from .requirement_spec import detect_output_language
                self.output_language = detect_output_language(
                    self.simulation_requirement, self.research_report, self.situation_brief)
            except Exception:  # noqa: BLE001 — never block construction on language sniff
                self.output_language = "English"
        # T4.6/T4.7: 情景标签（what-if 框架）+ base 模拟 id（反事实对比）
        self.scenario_label = (scenario_label or "").strip()
        self.base_simulation_id = base_simulation_id or None
        # VIZ-2: 图表清单（charts.json：{title, caption, source_data}），由编排器随 handoff 钉入。
        # 仅存储、不在此进一步接线（报告链按需消费）；缺省 None 时行为与旧构造逐字节一致。
        self.charts_manifest = charts_manifest
        # VIZ-2: 研究期图表清单渲染成「可引用图表」块，钉进各章节提示词（章节据此用标准 markdown
        # 图片语法引用图形）；charts_manifest 缺省/空/解析失败时为空串 → 注入自动跳过（degrade-safe）。
        try:
            self._charts_block = self._build_charts_block()
        except Exception:  # noqa: BLE001 — 图表清单为可选增强，绝不阻断构造
            self._charts_block = ""
        self._background_block = self._build_background_block()
        self._sources_index = self._build_sources_index()
        # EXECPLAN2 I-3-2: 模拟量化信号包（确定性接地下限），懒构建一次后缓存；
        # 关闭 REPORT_SIGNAL_PACK 时始终为空串，_prepend_research_background 自动跳过（行为不变）。
        self._signal_pack = ""
        # 预测市场信号包（Polymarket 公开 Gamma API，keyless）：市场隐含概率作为**校准锚点**
        # 注入章节/骨架/二元预测提示词。优先读研究 handoff 的 prediction_markets.json，
        # 缺失时经 PolymarketClient 现抓。懒构建一次后缓存；无数据/关闭
        # PREDICTION_MARKETS_ENABLED 时为空串，注入自动跳过（degrade-safe，行为不变）。
        self._market_pack = ""
        self._prediction_markets: List[Dict[str, Any]] = []
        # PM-3：市场快照是否「陈旧」（实时重报价未生效——关闭旗标/client 不可用/整体失败）。
        # True 时 _build_market_pack 在包头附一句时效性说明，读者知道价是研究期而非当下（degrade-safe）。
        self._markets_stale = False
        # NEXTSTEPS P0-1: 预测骨架（情景+概率+判定标准），在章节生成前从信号包+forecast_inputs
        # 推导一次，注入每章提示词让叙事对齐可证伪目标；缺省/未开时为空，_prepend 自动跳过。
        self._forecast_spine: Optional[Dict[str, Any]] = None
        self._forecast_spine_block = ""
        # XRUN-5/RPT-8: 报告级紧凑检索查询（懒派生一次后缓存）；None=未派生。
        self._retrieval_query: Optional[str] = None
        # RQ-1(4): 报告形状（章节数区间 / 每章字数 / 每章工具预算），从需求书 page_budget 懒派生
        # 一次后缓存；None=未派生（见 _report_shape）。小 page_budget→紧凑，无/大→展开默认。
        self._report_shape_cache: Optional[Dict[str, int]] = None
        # RPT-2(a): 大纲规划的 LLM 调用是否失败降级为默认大纲（系统性 LLM 故障的前哨信号）。
        self._outline_degraded = False
        # RPT-5: 大纲摘要（generate_report 规划完成后回填），供引用溯源审计豁免系统注入的
        # 摘要 blockquote（assemble_full_report 固定输出 "> {outline.summary}"）。
        self._outline_summary = ""

        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # 工具定义
        self.tools = self._define_tools()
        # interview_agents 需要 OASIS 模拟环境在线（IPC）。报告阶段几乎总在模拟结束、环境关闭之后运行，
        # 此时每次采访都会失败并浪费一轮昂贵的 LLM 工具调用（实测每章被反复 nudge 去采访→失败→回退）。
        # 在 agent 初始化时探活一次：环境不在线就直接从工具集中移除 interview_agents，模型再也看不到它。
        # 环境在线（极少数边跑场景）则保留。探活失败一律按「不可用」处理（degrade-safe）。
        if "interview_agents" in self.tools and not self._interview_env_alive():
            self.tools.pop("interview_agents", None)
            logger.info("报告: OASIS 环境未在线，已从工具集移除 interview_agents（避免每章无效采访重试）")

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

        # RQ-1(4): 依据需求书 page_budget 收敛/展开本次报告的每章工具预算——小 page_budget 报告回到
        # 紧凑 8 次，无/大预算用展开默认（Config.REPORT_AGENT_MAX_TOOL_CALLS=12）。以实例属性覆盖类
        # 默认，section-gen 仍读 self.MAX_TOOL_CALLS_PER_SECTION（测试经 __new__ 直接赋值，不受影响）。
        try:
            self.MAX_TOOL_CALLS_PER_SECTION = self._report_shape()["tool_budget"]
        except Exception:  # noqa: BLE001 — 形状派生失败保留类默认（degrade-safe）
            pass

        logger.info(f"ReportAgent 初始化完成: graph_id={graph_id}, simulation_id={simulation_id}")

    def _interview_env_alive(self) -> bool:
        """探活 OASIS 模拟 IPC 环境是否在线（interview_agents 的前置依赖）。

        报告阶段通常在模拟结束、环境关闭后运行，此时采访必失败。一次性探活，离线即返回 False，
        调用方据此从工具集移除 interview_agents。任何异常/缺失一律视为「不在线」（degrade-safe）。
        """
        try:
            if not self.simulation_id:
                return False
            import os as _os
            from .simulation_runner import SimulationRunner
            from .simulation_ipc import SimulationIPCClient
            sim_dir = _os.path.join(SimulationRunner.RUN_STATE_DIR, self.simulation_id)
            if not _os.path.isdir(sim_dir):
                return False
            return bool(SimulationIPCClient(sim_dir).check_env_alive())
        except Exception:  # noqa: BLE001 — 探活失败按不可用处理
            return False

    def _report_shape(self) -> Dict[str, int]:
        """RQ-1(4): 报告形状（章节数区间 / 每章字数下限+目标区间 / 每章工具预算）。

        从需求书 page_budget（parse_requirement_spec）懒派生一次后缓存。展开默认取自 Config
        （REPORT_SECTION_FLOOR_CHARS / report_section_target_chars() / REPORT_AGENT_MAX_TOOL_CALLS +
        OUTLINE_MIN/MAX_SECTIONS 类默认），小 page_budget 报告经纯函数 derive_report_shape 收敛回
        紧凑形状。spec 解析失败/无 page_budget → 展开默认（degrade-safe）。"""
        cached = getattr(self, "_report_shape_cache", None)
        if cached is not None:
            return cached
        page_budget: Optional[int] = None
        try:
            from .requirement_spec import parse_requirement_spec
            page_budget = parse_requirement_spec(
                getattr(self, "simulation_requirement", "") or "",
                getattr(self, "research_report", "") or "",
            ).get("page_budget")
        except Exception:  # noqa: BLE001 — spec 解析失败按「无预算」走展开默认
            page_budget = None
        target_lo, target_hi = Config.report_section_target_chars()
        shape = derive_report_shape(
            page_budget,
            expanded_min_sections=self.OUTLINE_MIN_SECTIONS,
            expanded_max_sections=self.OUTLINE_MAX_SECTIONS,
            expanded_floor_chars=int(getattr(Config, "REPORT_SECTION_FLOOR_CHARS", 2000) or 2000),
            expanded_target_lo=target_lo,
            expanded_target_hi=target_hi,
            expanded_tool_budget=int(getattr(Config, "REPORT_AGENT_MAX_TOOL_CALLS", 12) or 12),
        )
        try:
            self._report_shape_cache = shape
        except Exception:  # noqa: BLE001 — 纯缓存写入失败无害
            pass
        return shape

    def _section_prompt_kwargs(self) -> Dict[str, int]:
        """RQ-1(2)/(4)：模板进 SECTION 提示词的篇幅+工具调用范围槽位（随报告形状伸缩）。

        min/max_tool_calls 用实例的下限/上限（后者在真实报告 = 形状 tool_budget；测试经 __new__
        直接赋值时按其覆盖值），section_floor/target 取自形状。供两条 section-gen 路径复用。"""
        shape = self._report_shape()
        return {
            "min_tool_calls": self.MIN_TOOL_CALLS_PER_SECTION,
            "max_tool_calls": self.MAX_TOOL_CALLS_PER_SECTION,
            "section_floor_chars": shape["floor_chars"],
            "section_target_lo": shape["target_lo"],
            "section_target_hi": shape["target_hi"],
        }

    # ──────────────────────────────────────────────────────────────
    # VIZ-2: 研究期图表清单（charts.json：{title, caption, source_data}）
    # ──────────────────────────────────────────────────────────────
    def _available_charts(self) -> List[Dict[str, str]]:
        """VIZ-2：把 self.charts_manifest 规整成可引用图表条目 [{title, caption, path}]。

        charts_manifest 由编排器随研究 handoff 钉入（charts.json，形如
        [{title, caption, source_data}]，也容忍 {"charts": [...]} 包装或缺字段）。逐条规整：
          * title / caption 取字符串（缺失留空串）；
          * path 依次探测常见图形路径键（path/image/figure/file/png/svg/src/source_data），
            取第一个非空值 → 章节可用标准 markdown 图片语法 ![caption](path) 引用；
          * title / caption / path 全空的条目丢弃（无可引用信息）。
        纯函数、无副作用、degrade-safe：manifest 缺省/非列表/元素非字典 → []（绝不抛）。"""
        manifest = getattr(self, "charts_manifest", None)
        if isinstance(manifest, dict):  # 容忍 {"charts": [...]} 包装
            manifest = manifest.get("charts") or manifest.get("figures")
        if not isinstance(manifest, list):
            return []
        _path_keys = ("path", "image", "figure", "file", "png", "svg", "src", "source_data")
        out: List[Dict[str, str]] = []
        for e in manifest:
            if not isinstance(e, dict):
                continue
            title = str(e.get("title") or e.get("name") or "").strip()
            caption = str(e.get("caption") or e.get("description") or "").strip()
            path = ""
            for k in _path_keys:
                v = str(e.get(k) or "").strip()
                if v:
                    path = v
                    break
            if not (title or caption or path):
                continue  # 三者全空 → 无可引用信息，丢弃
            out.append({"title": title, "caption": caption, "path": path})
        return out

    def _build_charts_block(self) -> str:
        """VIZ-2：把可引用研究期图表渲染成钉进各章节提示词的「可引用图表」块。

        列出每个图表的标题/说明/相对路径，并示范标准 markdown 图片语法，让章节在相关处以
        ![说明](路径) 引用真实图形，而非凭空描述。无可用图表 → ""（注入自动跳过，行为不变）。"""
        charts = self._available_charts()
        if not charts:
            return ""
        zh = not str(getattr(self, "output_language", "") or "English").lower().startswith("en")
        if zh:
            lines = ["【可引用研究期图表（VIZ-2；相关处用 ![说明](相对路径) 引用真实图形）】"]
        else:
            lines = ["[Available research figures (VIZ-2; reference with "
                     "![caption](relative/path) where relevant)]"]
        for i, c in enumerate(charts, 1):
            title = c.get("title") or c.get("caption") or (f"figure {i}")
            seg = f"{i}. {title}"
            if c.get("caption") and c.get("caption") != title:
                seg += f" — {c['caption']}"
            if c.get("path"):
                seg += f"  ![{c.get('caption') or title}]({c['path']})"
            lines.append(seg)
        return "\n".join(lines)

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

    def _compact_retrieval_query(self) -> str:
        """XRUN-5/RPT-8: 派生一次报告级紧凑检索查询并缓存。

        整段需求书（3388 字）被直接当 query 时会被长度钳制砍到只剩开头修辞句，
        报告阶段杠杆最高的几次检索（大纲上下文 / InsightForge 全局扫描）从此全部
        锚定在引言上。这里按句界取核心问题前缀 + 追加高显著度 actor 名（让实体词
        进入 BM25/嵌入）。REPORT_COMPACT_RETRIEVAL_QUERY=False 时原样返回需求书
        （行为与历史一致）；任何失败也退回原文（degrade-safe）。
        """
        if getattr(self, "_retrieval_query", None) is not None:
            return self._retrieval_query
        req = self.simulation_requirement or ""
        if not getattr(Config, "REPORT_COMPACT_RETRIEVAL_QUERY", True):
            self._retrieval_query = req
            return req
        try:
            from .zep_tools import compact_graph_query
            q = compact_graph_query(req, 280)
            if len(q) < len(" ".join(req.split())):
                try:
                    from ..utils import actors as _actors
                    rows = _actors.extract_actor_rows(self.actors)
                    eligible = [r for r in rows if _actors.is_agent_eligible(r)]
                    eligible.sort(key=lambda r: _actors.salience_score(r), reverse=True)
                    names: List[str] = []
                    for r in eligible:
                        nm = str((r or {}).get("name", "") or "").strip()
                        if nm and nm not in names:
                            names.append(nm)
                        if len(names) >= 6:
                            break
                    if names:
                        q = (q + " " + " ".join(names))[:360]
                except Exception:  # noqa: BLE001 — actor 名为可选增强
                    pass
                logger.info(f"已派生紧凑检索查询（{len(req)}→{len(q)} 字符）: {q[:80]}...")
            self._retrieval_query = q or req
        except Exception:  # noqa: BLE001 — 派生失败退回原需求书
            self._retrieval_query = req
        return self._retrieval_query

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

        预测市场：还钉入市场信号包（self._market_pack，Polymarket 公开 Gamma API 的
        隐含概率表）——涉及概率的论断须与市场锚点对照。为空时自动跳过（行为与历史一致）。

        VIZ-2：还钉入可引用图表块（self._charts_block，研究期 charts.json）——章节可用标准
        markdown 图片语法引用真实图形。为空时自动跳过（行为与历史一致）。
        """
        # 市场包/图表块经 getattr 读取：部分离线测试用 __new__ 绕过 __init__ 构造 agent，
        # 缺属性时按空串处理（与注入跳过语义一致）。
        prefix_parts = [p for p in (
            self._background_block, self._sources_index,
            self._forecast_spine_block, self._signal_pack,
            getattr(self, "_market_pack", ""),
            getattr(self, "_charts_block", ""),
        ) if p]
        if not prefix_parts:
            return prompt
        return "\n\n".join(prefix_parts) + "\n\n" + prompt

    def _prior_section_char_budget(self, floor_chars: int = 8000, n_items: int = 1) -> int:
        """RQ-7 / I-6-4：前序章节上下文切片的预算化字符上限。

        ADAPTIVE_CONTEXT=true 时按**当前提供方的上下文窗口**放宽——大窗口模型
        （MiniMax-M3 512K / DeepSeek 1M）携带全量前文以增强跨章连贯与 grounding；
        小窗口/未知提供方（回退 32K）守住今天的 floor_chars 固定切片。share/num_items
        把前序章节这一类上下文限制在窗口的一个保守份额内、并按章节数平摊，避免 N 章 × 大切片
        撑爆窗口。ADAPTIVE_CONTEXT 关闭或任何异常 → 返回 floor_chars（degrade-safe，
        与历史逐字节一致）。返回值恒 >= floor_chars（token_budget.slice_budget_chars 的 floor 语义）。
        """
        if not getattr(Config, "ADAPTIVE_CONTEXT", False):
            return floor_chars
        try:
            from ..utils import token_budget as _tb
            provider = getattr(self.llm, "provider", None) or Config.LLM_PROVIDER
            window = Config.context_window_for(provider)
            return _tb.slice_budget_chars(
                window_tokens=window,
                reserved_tokens=getattr(Config, "RESERVED_COMPLETION_TOKENS", 8192),
                floor_chars=floor_chars,
                share=0.5,           # 前序章节须与系统提示/工具回包/补全共享窗口，取保守半额
                num_items=max(1, int(n_items)),
            )
        except Exception as _e:  # noqa: BLE001 — 预算化仅为增强，任何失败退回固定切片
            logger.debug(f"前序章节预算化切片回退固定值 {floor_chars}: {_e}")
            return floor_chars

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
        # 1) 量化结果（Top actor / 逐轮动作量 + 峰值 / 动作类型分布）——RQ-4：截断到 ~3600 字
        try:
            outcomes = self.zep_tools.simulation_outcomes(self.simulation_id, top_n=8)
            if outcomes and not outcomes.strip().startswith("（"):
                parts.append(outcomes[:3600])
        except Exception as e:  # noqa: BLE001 — 信号包为可选增强，失败仅告警不影响主流程
            logger.warning(f"信号包 simulation_outcomes 计算失败（忽略）: {e}")
        # 2) 派系/联盟结构——RQ-4：截断到 ~1600 字
        try:
            coalitions = self.zep_tools.coalition_map(self.graph_id, self.simulation_id)
            if coalitions and not coalitions.strip().startswith("（"):
                parts.append(coalitions[:1600])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"信号包 coalition_map 计算失败（忽略）: {e}")
        # 2b) R2-KG-7: 因果骨架——chokepoint 多跳因果邻域 + 最强 source→outcome 路径（含
        # 方向/符号/强度/时滞）。RQ-4：默认开（Config.REPORT_CAUSAL_SPINE=True），且 graph 层
        # 多跳遍历有界、任意失败降级为空串，不影响信号包其余部分。
        if getattr(Config, "REPORT_CAUSAL_SPINE", True):
            try:
                cs_blk = self._build_causal_spine_block()
                if cs_blk:
                    parts.append(cs_blk)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"信号包 causal_spine 计算失败（忽略）: {e}")
        # 3) 反事实差异（仅情景报告有基线时）——RQ-4：截断到 ~2400 字
        if self.base_simulation_id:
            try:
                diff = self.zep_tools.scenario_diff(self.base_simulation_id, self.simulation_id)
                if diff and not diff.strip().startswith("（"):
                    parts.append(diff[:2400])
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
    # 预测市场信号包（Polymarket 公开 Gamma API；市场隐含概率 = 校准锚点）
    # ──────────────────────────────────────────────────────────────
    def _load_prediction_markets(self) -> List[Dict[str, Any]]:
        """加载本次运行的预测市场快照（规整化 schema，见 utils.prediction_markets）。

        优先级：① 研究阶段落盘的 handoff/prediction_markets.json（经 PipelineManager 按
        simulation_id 定位，与 load_research_dossier_for_simulation 同模式）；② 文件缺失
        且 PolymarketClient 可用时现抓一次（检索词由需求书 + hot_topics + 头部 actor 名确定性
        派生）。任何失败返回 []（degrade-safe，绝不阻断报告生成）。
        """
        try:
            max_n = int(getattr(Config, "PREDICTION_MARKETS_MAX", 20) or 20)
        except (TypeError, ValueError):
            max_n = 20
        # ① 研究 handoff 产物（延迟导入避免与 pipeline_orchestrator 的模块级循环依赖）。
        try:
            from .pipeline_orchestrator import PipelineManager
            for entry in PipelineManager.list_pipelines():
                pid = entry.get("pipeline_id")
                if not pid:
                    continue
                data = PipelineManager.load(pid)
                if not data or data.get("simulation_id") != getattr(self, "simulation_id", None):
                    continue
                hd = data.get("handoff_dir") or PipelineManager.handoff_dir(pid)
                path = os.path.join(hd, "prediction_markets.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    markets = payload.get("markets") if isinstance(payload, dict) else payload
                    rows = [m for m in (markets or []) if isinstance(m, dict)]
                    if rows:
                        # PM-3：handoff-PLUS-refresh——研究期快照拉进来后对其做一次实时重报价，
                        # 保留 price_at_research 并算 Δ；重报价未生效时用研究期价并置 _markets_stale。
                        return self._requote_snapshot(rows[:max_n])
                break  # 找到对应管线即停（无论有无市场文件），转现抓兜底
        except Exception as e:  # noqa: BLE001 — handoff 读取失败转现抓兜底
            logger.debug(f"读取 handoff prediction_markets.json 失败（转现抓兜底）: {e}")
        # ② 现抓兜底（仅当 client 可用；一次快照，15s 超时 + 单次重试在 client 内部）。
        try:
            from ..utils.prediction_markets import PolymarketClient, derive_market_queries
            client = PolymarketClient()
            if not client.enabled:
                return []
            hot_topics: List[str] = []
            actor_names: List[str] = []
            _act = getattr(self, "actors", None)
            if isinstance(_act, dict):
                hot_topics = [str(t) for t in (_act.get("hot_topics") or []) if t]
                try:
                    from ..utils import actors as _actors
                    rows = [r for r in _actors.extract_actor_rows(_act)
                            if _actors.is_agent_eligible(r)]
                    rows.sort(key=lambda r: _actors.salience_score(r), reverse=True)
                    actor_names = [str((r or {}).get("name", "") or "").strip()
                                   for r in rows]
                    actor_names = [n for n in actor_names if n]
                except Exception:  # noqa: BLE001 — actor 名为查询词的可选增强
                    pass
            queries = derive_market_queries(getattr(self, "simulation_requirement", "") or "",
                                            hot_topics=hot_topics,
                                            actor_names=actor_names)
            if not queries:
                return []
            try:
                min_vol = float(getattr(Config, "PREDICTION_MARKETS_MIN_VOLUME", 200) or 200)
            except (TypeError, ValueError):
                min_vol = 200.0
            try:
                max_per_event = int(getattr(Config, "PREDICTION_MARKETS_MAX_PER_EVENT", 3) or 3)
            except (TypeError, ValueError):
                max_per_event = 3
            markets = client.snapshot_for_queries(queries, max_total=max_n,
                                                  min_volume=min_vol,
                                                  max_per_event=max_per_event)
            if markets:
                logger.info(f"预测市场现抓兜底：{len(markets)} 个活跃市场（queries={queries}）")
            self._markets_stale = False  # PM-3：现抓即实时价，不陈旧
            return markets
        except Exception as e:  # noqa: BLE001 — 市场信号为可选增强
            logger.warning(f"预测市场信号抓取失败（忽略）: {e}")
            return []

    def _requote_snapshot(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """PM-3：对研究期市场快照做一次实时重报价（PREDICTION_MARKETS_REQUOTE，默认开）。

        走 PolymarketClient.requote_markets：当前 "Yes" 价覆盖 implied_yes_prob、保留研究期价
        price_at_research、算 price_delta——读者能看到「34%→41%」的移动，判断锚点是否还成立。
        整体成败写入 self._markets_stale（供 _build_market_pack 在包头标注时效性）：
          * 关闭旗标 / client 不可用 / 无行 / 异常 → 保留研究期快照并置 stale=True（degrade-safe）；
          * 每行局部失败由 requote_markets 自带 requote_failed 标记，不影响整体判定。
        返回（可能重报价后的）行列表，绝不抛异常。"""
        rows = [m for m in (markets or []) if isinstance(m, dict)]
        if not rows:
            self._markets_stale = False
            return rows
        if not getattr(Config, "PREDICTION_MARKETS_REQUOTE", True):
            self._markets_stale = True  # 关闭重报价 → 用研究期价（包头标注时效性）
            return rows
        try:
            from ..utils.prediction_markets import PolymarketClient
            client = PolymarketClient()
            if not client.enabled:
                self._markets_stale = True
                return rows
            requoted = client.requote_markets(rows)
            if not requoted:
                self._markets_stale = True
                return rows
            # 全部行都重报价失败（网络整体故障）→ 视作整体陈旧；否则至少部分拿到现价。
            self._markets_stale = all(m.get("requote_failed") for m in requoted)
            return requoted
        except Exception as e:  # noqa: BLE001 — 重报价为可选增强，失败保留研究期价并标注
            logger.warning(f"预测市场实时重报价失败（保留研究期价并标注时效性）: {e}")
            self._markets_stale = True
            return rows

    def _refresh_market_prices_for_extraction(self) -> None:
        """PM-3：二元预测抽取前对已缓存快照再重报价一次，使 market_anchor 用现价。

        就地更新 self._prediction_markets（供 extract_binary_forecasts 的 markets 回填/校验）
        与 self._market_pack（渲染的市场表，Δ 列随之刷新）。无缓存快照/未开旗标/失败 → 原样
        （degrade-safe，_requote_snapshot 内部已把整体成败写进 _markets_stale）。"""
        rows = getattr(self, "_prediction_markets", None)
        if not rows or not getattr(Config, "PREDICTION_MARKETS_ENABLED", True):
            return
        refreshed = self._requote_snapshot(rows)
        self._prediction_markets = refreshed
        try:
            self._market_pack = self._render_market_pack(refreshed)
        except Exception as e:  # noqa: BLE001 — 重渲染失败保留旧市场包
            logger.warning(f"重报价后市场包重渲染失败（保留旧市场包）: {e}")

    def _render_market_pack(self, markets: List[Dict[str, Any]]) -> str:
        """把规整化市场快照渲染为「预测市场信号包」（表 + 对照指令 + PM-3 时效性说明）。

        纯渲染（无网络/无 LLM）：PM-2 渲染行数 12→20；self._markets_stale 为真时在包头附一句
        「实时重报价未生效，价为研究期快照」的时效性说明。空/无表 → ""（注入自动跳过）。"""
        rows = [m for m in (markets or []) if isinstance(m, dict)]
        if not rows:
            return ""
        from ..utils.prediction_markets import render_markets_block
        lang = ("en" if str(getattr(self, "output_language", "") or "English")
                .lower().startswith("en") else "zh")
        table = render_markets_block(rows[:20], lang=lang)  # PM-2：渲染行数 12→20
        if not table:
            return ""
        header = (
            "【预测市场信号（Polymarket 实盘隐含概率·校准锚点，非真值）】\n"
            "以下为与本预测问题相关的真实预测市场当前定价（机器抓取）。撰写涉及概率的论断时"
            "须与之对照：与所列市场重叠的预测应引用其隐含概率，偏离超过 10 个百分点时须显式"
            "解释分歧依据（市场遗漏/错价了什么）。市场价格是聚合信念，不是事实真值。"
        )
        if getattr(self, "_markets_stale", False):
            header += (
                "\n（注：实时重报价未生效，以下为研究期快照价格，可能已随时间漂移；"
                "引用前请注意时效性。）")
        return header + "\n\n" + table

    def _build_market_pack(self) -> str:
        """组装「预测市场信号包」：市场隐含概率表 + 对照指令，钉进章节/骨架/二元预测提示词。

        计算一次后缓存市场快照于 self._prediction_markets（二元预测的 market_anchor 回填
        复用同一份快照）。PM-3：_load_prediction_markets 已对 handoff 快照做实时重报价并写
        _markets_stale。无数据/关闭旗标时返回 ""（注入自动跳过，行为与历史一致）。
        """
        if not getattr(Config, "PREDICTION_MARKETS_ENABLED", True):
            return ""
        markets = self._load_prediction_markets()
        if not markets:
            return ""
        self._prediction_markets = markets
        return self._render_market_pack(markets)

    def _build_causal_spine_block(self, max_chokepoints: int = 4, max_chars: int = 3200) -> str:
        """R2-KG-7: 确定性「因果骨架」块——以图谱中显著度最高的若干 chokepoint 为中心，渲染其
        多跳因果邻域，并追踪最强 source→outcome（前两位支点之间）传导路径，钉进信号包。

        支点取自研究 actors 的能动角色按显著度降序（与关系名册同源、确定性）。每个支点经
        zep_tools.trace_cascade(center=...) 取其因果邻域；再以前两位支点为 source/target 取
        二者间的有向因果路径——边的方向/符号/强度/时滞由 graph 层 runtime 投影 schema 渲染。

        Degrade-safe：无 actors / 无能动角色 / 遍历失败 / 无路径 → 返回 ""，信号包其余部分不受影响。
        仅在 REPORT_CAUSAL_SPINE 开启时由 _build_signal_pack 调用；多跳遍历在 graph 层有界。
        """
        try:
            from ..utils import actors as _actors
            rows = _actors.extract_actor_rows(self.actors)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        try:
            eligible = [r for r in rows if _actors.is_agent_eligible(r)]
            eligible.sort(key=lambda r: _actors.salience_score(r), reverse=True)
        except Exception:  # noqa: BLE001 — 排序/过滤失败时退回原始顺序
            eligible = rows
        centers: List[str] = []
        for r in eligible:
            nm = str((r or {}).get("name", "") or "").strip()
            if nm and nm not in centers:
                centers.append(nm)
            if len(centers) >= max_chokepoints:
                break
        if not centers:
            return ""
        segs: List[str] = []
        # 1) 各 chokepoint 的多跳因果邻域（"哪个节点一动就翻盘"的结构）
        for nm in centers:
            try:
                neigh = self.zep_tools.trace_cascade(self.graph_id, center=nm, causal_only=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"因果骨架 trace_cascade(center={nm}) 失败（忽略）: {e}")
                continue
            if neigh and not neigh.strip().startswith("（"):
                segs.append(neigh.strip())
        # 2) 最强 source→outcome 传导路径（前两位支点之间的有向因果链）
        if len(centers) >= 2:
            try:
                path = self.zep_tools.trace_cascade(
                    self.graph_id, source=centers[0], target=centers[1], causal_only=True
                )
                if path and not path.strip().startswith("（"):
                    segs.append(path.strip())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"因果骨架 trace_cascade(source→target) 失败（忽略）: {e}")
        if not segs:
            return ""
        body = "\n\n".join(segs)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + " …"
        header = (
            "【因果骨架（确定性·图谱多跳，chokepoint 为情景分叉支点）】\n"
            "以下为图谱中显著度最高节点的因果邻域与最强传导路径（边尽量标注方向/符号/强度/时滞），"
            "撰写时据此解释「哪个节点一动就翻盘」的级联机制，而非仅罗列声量数字。"
        )
        return header + "\n" + body

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
            # 预测市场信号包：优先复用已构建的（generate_report 已先构建），否则为骨架
            # 单独构建一次（现抓/读 handoff 皆 degrade-safe，失败为空串 → 提示词不变）。
            market_pack = getattr(self, "_market_pack", "")
            if not market_pack and getattr(Config, "PREDICTION_MARKETS_ENABLED", True):
                try:
                    market_pack = self._build_market_pack()
                except Exception:  # noqa: BLE001
                    market_pack = ""
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
                market_block=market_pack,
            )
            if not spine or not spine.get("scenarios"):
                logger.info("预测骨架推导未产出情景，跳过（回退为成稿后抽取）")
                return
            # RPT-3（REPORT_CRITIQUE_BEFORE_PROSE，默认开）：红队自校准 + 事前验尸挪到
            # 叙事之前——此前批判发生在全部正文写完之后，正文（连章节标题里的百分比）
            # 捍卫的是 4 情景 39/23/19/19，而 forecast.json 交付的是批判后的 5 情景
            # 34/22/16/14/14，按构造必然矛盾（S11 只能事后打标）。前置后，钉进各章
            # 提示词的骨架与最终 forecast.json 按构造一致。critiqued 标记数据驱动，
            # _finalize 据此跳过二次批判（premortem 内部自带 REPORT_PREMORTEM 门）。
            if (getattr(Config, "REPORT_CRITIQUE_BEFORE_PROSE", True)
                    and getattr(Config, "REPORT_FORECAST_SELF_CRITIQUE", False)):
                try:
                    _critiqued = _fe.self_critique_forecast(spine, self.llm)
                    _critiqued = _fe.premortem_forecast(_critiqued, self.llm)
                    if _critiqued.get("scenarios"):
                        spine = _critiqued
                        logger.info(
                            f"预测骨架已先于叙事完成红队自校准（{len(spine['scenarios'])} 情景）"
                        )
                except Exception as _ce:  # noqa: BLE001 — 批判失败沿用未批判骨架
                    logger.warning(f"骨架前置自校准失败（忽略）: {_ce}")
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

    def _finalize_structured_forecast(self, report_id: str, report_markdown: str,
                                      report: Optional["Report"] = None) -> None:
        """Persist the final forecast.json.

        Prefers the pre-derived spine (P0-1, already MECE & signal-seeded); else
        extracts post-hoc from prose (legacy path). Then optional red-team self-critique
        (P2-1), citation-grounding audit, and the publish gate (P2-3). Caller wraps in
        try/except → degrade-safe.

        RQ-2：审计跑完后（发布门之前），若开启 REPORT_REPAIR_PASSES 且某维度失败，按维度做
        一次定向修复（引用回填 / 引文接地 / 占位符解析），重跑受影响审计一次，把 before/after
        合并进 forecast['quality']['repair']。拿到 ``report`` 对象时把修复后的 markdown 回写
        report.markdown_content 与 full_report.md（供下游 Part-1/三部/判定章节接续）；缺省 None
        时仅记录（degrade-safe，兼容既有仅传 report_id/markdown 的调用方与测试）。
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
        # RPT-3: 骨架已在叙事前完成批判（critiqued=True，数据驱动判断）时不再二次批判——
        # 成稿后再动概率会让正文与 forecast.json 按构造矛盾。非骨架路径（成稿后抽取，
        # 如骨架推导失败的 91f5 型运行）保留原有的成稿后批判。
        if (getattr(Config, "REPORT_FORECAST_SELF_CRITIQUE", False)
                and not forecast.get("critiqued")):
            forecast = self_critique_forecast(forecast, self.llm)
        # XRUN-16(1): 骨架情景数与最终情景数漂移检测（正文按骨架 N 情景撰写、交付却是
        # M 情景 ⇒ 必然矛盾）；随 forecast.json quality 落盘供健康门消费。
        try:
            _pin_n = len((self._forecast_spine or {}).get("scenarios") or [])
            _fin_n = len(forecast.get("scenarios") or [])
            if _pin_n and _fin_n and _pin_n != _fin_n:
                forecast.setdefault("quality", {})["scenario_count_drift"] = {
                    "pinned": _pin_n, "final": _fin_n}
                logger.warning(f"情景数漂移：骨架 {_pin_n} 情景 → 最终 {_fin_n} 情景（正文与交付可能矛盾）")
        except Exception:  # noqa: BLE001
            pass
        forecast["citation_audit"] = audit_citation_grounding(report_markdown)
        # QUALITY-OPT S2: flag quotes presented as real but neither labeled simulation nor found
        # in the research material (laundered sim/graph text or fabrication). Observability →
        # forecast.quality.quote_provenance; loud warning when any are found.
        try:
            _qp = self._audit_quote_provenance(report_markdown)
            if _qp:
                forecast.setdefault("quality", {})["quote_provenance"] = _qp
                if _qp.get("ungrounded"):
                    logger.warning(
                        f"引用接地审计：{_qp['ungrounded']}/{_qp['total_quotes']} 条引用未标注为模拟"
                        f"且未在研究材料中匹配（疑似嫁接/捏造）: {_qp.get('examples', [])[:2]}")
                # RPT-5: 带 [S#] 来源但非逐字的引文单独告警（缺陷但非捏造，不入发布门）。
                if _qp.get("cited_unverbatim"):
                    logger.warning(
                        f"引用接地审计：{_qp['cited_unverbatim']}/{_qp['total_quotes']} 条引用带 [S#] "
                        f"来源但疑似非逐字引用: {_qp.get('unverbatim_examples', [])[:2]}")
        except Exception:  # noqa: BLE001
            pass
        # QUALITY-OPT S11: flag prose scenario-probabilities that disagree with forecast.json.
        try:
            _nc = self._audit_numeric_consistency(report_markdown, forecast)
            if _nc.get("mismatch_count"):
                forecast.setdefault("quality", {})["numeric_consistency"] = _nc
                logger.warning(f"概率一致性审计：{_nc['mismatch_count']} 处正文概率与 forecast.json 不符: "
                               f"{_nc.get('scenario_prob_mismatches', [])[:3]}")
        except Exception:  # noqa: BLE001
            pass
        # QUALITY-OPT S12: flag implausible headline growth stats (>100% YoY) that anchor reports.
        try:
            _sp = self._audit_stat_plausibility(report_markdown)
            if _sp.get("count"):
                forecast.setdefault("quality", {})["implausible_stats"] = _sp
                logger.warning(f"统计合理性审计：{_sp['count']} 处疑似不合理的极端增长率: "
                               f"{_sp.get('implausible_stats', [])[:3]}")
        except Exception:  # noqa: BLE001
            pass
        # QUALITY-OPT A1: emit >=N INDEPENDENT binary (yes/no) forecasts — the brief's headline
        # deliverable — ALONGSIDE the scenario spine. The research dossier usually already holds a
        # compliant F1..Fn table; we extract it (preserving its probabilities) and top up to the
        # minimum, with a conviction+objectivity scorecard. Language follows the report so the two
        # views stay consistent. Additive + degrade-safe: failure leaves the scenario forecast intact.
        if getattr(Config, "FORECAST_EMIT_BINARY", True):
            try:
                from .forecast_extractor import extract_binary_forecasts as _ebf
                _lang = (getattr(self, "output_language", None)
                         or getattr(Config, "DEERFLOW_RESEARCH_LANGUAGE", None) or "English")
                _bsrc = self.research_report or report_markdown or ""
                # RPT-6: 主题从配置/需求书派生，不再由抽取器硬编码 Bridgewater 三元组。
                _themes = getattr(Config, "FORECAST_BINARY_THEMES", None)
                if isinstance(_themes, str):
                    _themes = [t.strip().lower() for t in _themes.split(",") if t.strip()]
                if not _themes:
                    try:
                        from .requirement_spec import parse_requirement_spec
                        _themes = parse_requirement_spec(
                            self.simulation_requirement, self.research_report
                        ).get("themes") or None
                    except Exception:  # noqa: BLE001 — 主题派生失败退回主题无关措辞
                        _themes = None
                # XRUN-1(b): 注入模拟量化信号包（缺失时为骨架单独构建一次），让 Part-1 概率
                # 对模拟敏感——两次不同模拟产出逐字节相同的概率向量即证明此前模拟被完全忽略。
                _sig = self._signal_pack
                if not _sig and getattr(Config, "FORECAST_SIM_SENSITIVITY", True):
                    try:
                        _sig = self._build_signal_pack()
                    except Exception:  # noqa: BLE001
                        _sig = ""
                # PM-3：二元预测抽取前对已缓存快照再重报价一次，使 market_anchor 用现价
                # （报告成稿到抽取之间市场会漂移）。就地刷新 _prediction_markets + _market_pack；
                # 无缓存/未开/失败 → 原样（degrade-safe，_refresh_market_prices_for_extraction 内部兜底）。
                try:
                    self._refresh_market_prices_for_extraction()
                except Exception:  # noqa: BLE001 — 重报价为增强，失败沿用旧快照
                    pass
                # 预测市场：市场表注入抽取提示词（重叠预测须引用隐含概率并解释 >10pt 分歧），
                # 规整化快照回填 market_anchor 的隐含概率。缺失时构建一次；失败为空（degrade-safe）。
                _mkt = getattr(self, "_market_pack", "")
                if not _mkt and getattr(Config, "PREDICTION_MARKETS_ENABLED", True):
                    try:
                        _mkt = self._build_market_pack()
                    except Exception:  # noqa: BLE001
                        _mkt = ""
                # B2: 需求书解析出的 binary_min_count 参与生效——取 spec 与 Config 的较大者
                # （需求书写明「15+ binary forecasts」时不被 Config 默认静默压低）。
                _bres = _ebf(
                    _bsrc, self.llm,
                    min_count=self._binary_min_count(),
                    language=_lang,
                    situation_brief=getattr(self, "situation_brief", None),
                    themes=_themes,
                    signal_pack=_sig or None,
                    market_pack=_mkt or None,
                    markets=getattr(self, "_prediction_markets", None) or None,
                )
                if _bres.get("binary_forecasts"):
                    forecast["binary_forecasts"] = _bres["binary_forecasts"]
                    forecast["binary_quality"] = _bres.get("binary_quality") or {}
                    # PM-2：确定性市场对照负载（预测 vs 市场隐含概率、|Δ|、>10pp 判定）——嵌入
                    # forecast.json 并独立落 market_comparison.json，供 Part-1 后的「Market Cross-Check」
                    # 渲染块与下游消费。无锚定预测时不写（degrade-safe）。
                    _mc = _bres.get("market_comparison")
                    if isinstance(_mc, dict) and _mc.get("comparisons"):
                        forecast["market_comparison"] = _mc
                        try:
                            _mcpath = os.path.join(
                                ReportManager._get_report_folder(report_id),
                                "market_comparison.json")
                            write_text_atomic(
                                _mcpath, json.dumps(_mc, ensure_ascii=False, indent=2))
                        except Exception as _mce:  # noqa: BLE001 — 落盘失败不影响主流程
                            logger.warning(f"落 market_comparison.json 失败（忽略）: {_mce}")
                    # XRUN-1(c): 与同图谱、不同模拟的上一份报告比对概率向量——
                    # 若逐项 |Δ|≤0.01 完全一致，则预测对模拟不敏感，记入 quality。
                    try:
                        _dup = self._check_binary_sim_sensitivity(report_id, forecast)
                        if _dup:
                            forecast.setdefault("quality", {})["sim_insensitivity"] = _dup
                            logger.warning(
                                f"二元预测对模拟不敏感：概率向量与报告 {_dup.get('other_report_id')} "
                                f"完全一致（不同 simulation_id）")
                    except Exception:  # noqa: BLE001 — 观测性检查，绝不影响产物
                        pass
            except Exception as _be:  # noqa: BLE001 — additive; never break finalization
                logger.warning(f"二元预测抽取失败（忽略，不影响情景预测）: {_be}")
        # RQ-2：质量门失败 → 按维度单次定向修复（引用回填 / 引文接地 / 占位符解析），
        # 重跑受影响审计一次并把 before/after 记进 forecast['quality']['repair']（合并，不覆盖）。
        # 置于发布门之前 ⇒ 发布门只对修复后的审计结果打分一次，避免二次降级。任何失败仅告警。
        if getattr(Config, "REPORT_REPAIR_PASSES", True):
            try:
                report_markdown = self._run_repair_passes(
                    report_id, forecast, report_markdown, report)
            except Exception as _rpe:  # noqa: BLE001 — 修复为旁路品控，失败不影响产物
                logger.warning(f"报告修复 passes 失败（忽略，不影响产物）: {_rpe}")
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

    # RQ-2：字面占位符引用记号（研究/撰写阶段留下的未解析槽位，如 [S?-a] / 【S?】 / [S#]）。
    # 与真正的 [S1]/[S2] 引用（_CITATION_RE 只认 S\d+）不同——这些永远不是有效引用，必须解析或删除。
    _PLACEHOLDER_TOKEN_RE = re.compile(r"[\[【]\s*S(?:\?|#)(?:-[A-Za-z0-9]+)?\s*[\]】]")

    def _run_repair_passes(self, report_id: str, forecast: Dict[str, Any],
                           report_markdown: str,
                           report: Optional["Report"] = None) -> str:
        """RQ-2：质量门失败时按维度做一次定向修复，重跑受影响审计一次并记录 before/after。

        触发维度（各自独立判定，仅修失败的那些）：
          * citation_backfill —— citation_audit.coverage < 发布门阈值（且确有定量声明）；
          * quote_grounding  —— quality.quote_provenance.ungrounded > 0；
          * placeholder      —— 正文含字面占位符记号（_PLACEHOLDER_TOKEN_RE）。
        修复后若 markdown 变化：重跑 citation/quote/S11/S12 审计覆盖旧值，并把 before/after
        合并进 forecast['quality']['repair']（never overwrite 其余 quality 键）。拿到 report
        对象时回写 report.markdown_content 与 full_report.md。返回（可能被修复的）markdown。"""
        from .forecast_extractor import audit_citation_grounding as _acg
        md = report_markdown or ""
        _q0 = forecast.get("quality")
        quality = dict(_q0) if isinstance(_q0, dict) else {}
        citation_audit = forecast.get("citation_audit") or {}
        try:
            min_cov = float(getattr(Config, "REPORT_PUBLISH_GATE_MIN_COVERAGE", 0.5) or 0.0)
        except (TypeError, ValueError):
            min_cov = 0.5
        cov = float(citation_audit.get("coverage", 1.0) or 0.0)
        has_quant = int(citation_audit.get("quantitative_claims", 0) or 0) > 0
        qp = quality.get("quote_provenance") or {}
        ungrounded0 = int(qp.get("ungrounded", 0) or 0)
        placeholder0 = len(self._PLACEHOLDER_TOKEN_RE.findall(md))

        need_citation = has_quant and cov < min_cov
        need_quote = ungrounded0 > 0
        need_placeholder = placeholder0 > 0
        if not (need_citation or need_quote or need_placeholder):
            return md

        before = {"citation_coverage": round(cov, 3),
                  "quote_ungrounded": ungrounded0,
                  "placeholder_tokens": placeholder0}
        passes: List[Dict[str, Any]] = []
        new_md = md
        if need_citation:
            new_md, n = self._repair_citation_backfill(new_md)
            passes.append({"dimension": "citation_backfill", "citations_inserted": n})
        if need_quote:
            new_md, n = self._repair_quote_grounding(new_md)
            passes.append({"dimension": "quote_grounding", "quotes_removed": n})
        if need_placeholder:
            new_md, n = self._repair_placeholder_tokens(new_md)
            passes.append({"dimension": "placeholder_resolution", "tokens_resolved": n})

        if new_md == md:
            # 命中维度但无处可修（如无匹配来源）——仍记录，before==after，不动 markdown。
            quality["repair"] = {"applied": False, "passes": passes,
                                 "before": before, "after": before}
            forecast["quality"] = quality
            return md

        # 重跑受影响审计一次（覆盖旧值，让发布门对修复后的状态打分）。
        new_ca = _acg(new_md)
        forecast["citation_audit"] = new_ca
        try:
            _qp2 = self._audit_quote_provenance(new_md)
            quality["quote_provenance"] = _qp2 or {"total_quotes": 0, "ungrounded": 0,
                                                   "examples": []}
        except Exception:  # noqa: BLE001
            pass
        try:
            _nc2 = self._audit_numeric_consistency(new_md, forecast)
            if _nc2.get("mismatch_count"):
                quality["numeric_consistency"] = _nc2
        except Exception:  # noqa: BLE001
            pass
        try:
            _sp2 = self._audit_stat_plausibility(new_md)
            if _sp2.get("count"):
                quality["implausible_stats"] = _sp2
        except Exception:  # noqa: BLE001
            pass
        after = {
            "citation_coverage": round(float(new_ca.get("coverage", cov) or 0.0), 3),
            "quote_ungrounded": int((quality.get("quote_provenance") or {}).get("ungrounded", 0) or 0),
            "placeholder_tokens": len(self._PLACEHOLDER_TOKEN_RE.findall(new_md)),
        }
        quality["repair"] = {"applied": True, "passes": passes,
                             "before": before, "after": after}
        forecast["quality"] = quality
        # 回写修复后的 markdown（仅在拿到 report 对象时；否则仅记录，degrade-safe）。
        if report is not None:
            report.markdown_content = new_md
            try:
                folder = ReportManager._get_report_folder(report_id)
                write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
            except Exception as _we:  # noqa: BLE001
                logger.warning(f"回写修复后 full_report.md 失败（忽略）: {_we}")
        logger.info(
            f"报告修复 passes: {report_id} 引用覆盖 {before['citation_coverage']}→"
            f"{after['citation_coverage']}，未接地引用 {before['quote_ungrounded']}→"
            f"{after['quote_ungrounded']}，占位符 {before['placeholder_tokens']}→"
            f"{after['placeholder_tokens']}"
        )
        return new_md

    def _source_haystacks(self) -> List[Tuple[str, str]]:
        """把 self.sources 渲染成 [(tag, 归一化底料)] 供引用回填匹配。tag 用位置式 [S{i}]
        （与 _CITATION_RE 认可的 S\\d+ 形式一致，回填后必被引用审计计入）；底料为该来源所有
        字符串字段（title/url/snippet/content/summary…）拼接后归一化。空来源返回 []。"""
        out: List[Tuple[str, str]] = []
        for i, s in enumerate(self.sources or [], 1):
            if not isinstance(s, dict):
                continue
            vals = [str(v) for v in s.values() if isinstance(v, (str, int, float))]
            hay = self._norm_quote_text(" ".join(vals))
            if hay:
                out.append((f"[S{i}]", hay))
        return out

    def _repair_citation_backfill(self, md: str) -> Tuple[str, int]:
        """RQ-2 引用回填：对缺引用的定量正文行，若其中某个数字/百分比逐字出现在某来源底料里，
        追加该来源的 [S{i}] 记号。保守：仅当数字（>=2 位或带 %）在来源中精确命中才回填，
        避免虚构归因；每行至多回填一个记号。返回 (新 markdown, 回填数)。"""
        haystacks = self._source_haystacks()
        if not haystacks:
            return md, 0
        num_re = re.compile(r"\d+(?:\.\d+)?%|\b\d{2,}(?:\.\d+)?\b")
        inserted = 0
        out_lines: List[str] = []
        for ln in md.splitlines():
            stripped = ln.strip()
            # 跳过标题 / 引用块 / 已带任意 [S…] 记号（位置式 [S1]、分层 [S1-a] 或占位符 [S?]）/ 无数字的行
            if (not stripped or stripped.startswith("#") or stripped.startswith(">")
                    or self._ANY_S_TAG_RE.search(ln)):
                out_lines.append(ln)
                continue
            nums = [m.group(0).lower() for m in num_re.finditer(stripped)]
            if not nums:
                out_lines.append(ln)
                continue
            matched_tag = None
            for tag, hay in haystacks:
                if any(n in hay for n in nums):
                    matched_tag = tag
                    break
            if matched_tag:
                out_lines.append(ln.rstrip() + " " + matched_tag)
                inserted += 1
            else:
                out_lines.append(ln)
        if not inserted:
            return md, 0
        return "\n".join(out_lines), inserted

    def _repair_quote_grounding(self, md: str) -> Tuple[str, int]:
        """RQ-2 引文接地修复：删除既非模拟标注、又未在研究材料中逐字命中、且不带 [S#] 来源的
        blockquote 行（S2 判定为嫁接/捏造的引文）。与 _audit_quote_provenance 同源判定，但按整行
        操作以便精确删除。返回 (新 markdown, 删除行数)。删除是最诚实且可度量的动作（重跑审计后
        未接地计数必降）。"""
        v2 = bool(getattr(Config, "REPORT_QUOTE_AUDIT_V2", True))
        ground_raw = ((self.research_report or "") + "\n" + (self._background_block or "")
                      + "\n" + (getattr(self, "situation_brief", "") or ""))
        ground = self._norm_quote_text(ground_raw) if v2 else ground_raw.lower()
        sim_labels = ("模拟", "simulation", "代理人", "推演", "sim-agent", "simulated agent")
        _summary_n = self._norm_quote_text(getattr(self, "_outline_summary", "") or "")
        _table_note_n = self._norm_quote_text(self._TABLE_NOTE_TEXT)

        def _is_ungrounded(raw_q: str) -> bool:
            if len(raw_q) < 12:
                return False
            ql = raw_q.lower()
            if any(t in ql for t in sim_labels):
                return False
            if self._S_CITATION_RE.search(raw_q):     # 带 [S#] 来源 → 不删（至多是非逐字，缺陷但非捏造）
                return False
            if not v2:
                probe = re.sub(r'^["“”「『\'\s]+', '', raw_q)[:40].lower().strip()
                return not (probe and probe in ground)
            qn = self._norm_quote_text(raw_q)
            if not qn:
                return False
            if (_summary_n and qn == _summary_n) or qn == _table_note_n:
                return False
            probes = [qn[:40]]
            if len(qn) > 80:
                mid = len(qn) // 2
                probes.append(qn[mid:mid + 40])
            if len(qn) > 40:
                probes.append(qn[-40:])
            return not any(p and p in ground for p in probes)

        removed = 0
        out_lines: List[str] = []
        for ln in md.splitlines():
            s = ln.strip()
            if s.startswith(">"):
                raw_q = s[1:].strip()
                if _is_ungrounded(raw_q):
                    removed += 1
                    continue  # 丢弃该 blockquote 行（不写入输出）
            out_lines.append(ln)
        if not removed:
            return md, 0
        return "\n".join(out_lines), removed

    def _repair_placeholder_tokens(self, md: str) -> Tuple[str, int]:
        """RQ-2 占位符解析：字面占位符记号（[S?-a] / 【S?】 / [S#]）——恰好只有一个来源时解析为
        [S1]，否则删除。删除后清理该行遗留的双空格与孤立标点。返回 (新 markdown, 处理数)。"""
        tokens = self._PLACEHOLDER_TOKEN_RE.findall(md)
        if not tokens:
            return md, 0
        n = len(tokens)
        single_source = len([s for s in (self.sources or []) if isinstance(s, dict)]) == 1
        if single_source:
            new_md = self._PLACEHOLDER_TOKEN_RE.sub("[S1]", md)
        else:
            new_md = self._PLACEHOLDER_TOKEN_RE.sub("", md)
            # 清理删除后遗留的 " ，" / 双空格（逐行，避免跨行误伤）
            new_md = "\n".join(
                re.sub(r"[ \t]{2,}", " ", re.sub(r"\s+([，。,.;；、])", r"\1", line)).rstrip()
                if self._PLACEHOLDER_TOKEN_RE.search(line) is None else line
                for line in new_md.splitlines()
            )
        return new_md, n

    # 任意 [S…] 记号（位置式 [S1]、分层 [S1-a]、或占位符 [S?]/[S#]）——引用回填判定「本行是否已带记号」。
    _ANY_S_TAG_RE = re.compile(r"[\[【]\s*S[\d?#]", re.I)

    def _check_binary_sim_sensitivity(self, report_id: str,
                                      forecast: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """XRUN-1(c): 廉价的跨报告同一性门。同一图谱、不同 simulation_id 的上一份报告若
        产出了逐项 |Δ|≤0.01 的相同二元概率向量，说明 Part-1 概率是研究先验的确定性复读、
        模拟阶段贡献为零（74dc vs aa77 逐字节相同即为实锤）。命中返回 quality 记录字典，
        否则 None。只读比对、任何失败返回 None（观测性，绝不影响主流程）。
        注：在 XRUN-1(b) 的信号注入生效前，本门可能对既有报告合法命中。"""
        probs = [b.get("probability") for b in (forecast.get("binary_forecasts") or [])
                 if isinstance(b, dict)]
        if len(probs) < 5:  # 向量太短时同一性没有区分力
            return None
        try:
            reports_dir = os.path.join(Config.UPLOAD_FOLDER, "reports")
            if not os.path.isdir(reports_dir):
                return None
            candidates = []
            for name in os.listdir(reports_dir):
                if name == report_id or not name.startswith("report_"):
                    continue
                meta_path = os.path.join(reports_dir, name, "meta.json")
                fc_path = os.path.join(reports_dir, name, "forecast.json")
                if os.path.exists(meta_path) and os.path.exists(fc_path):
                    candidates.append((os.path.getmtime(fc_path), name, meta_path, fc_path))
            # 只比对最近的少量报告（有界）
            for _, other_id, meta_path, fc_path in sorted(candidates, reverse=True)[:8]:
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    if str(meta.get("graph_id") or "") != str(self.graph_id or ""):
                        continue
                    if str(meta.get("simulation_id") or "") == str(self.simulation_id or ""):
                        continue  # 同一模拟的重跑本就应相近，不算不敏感
                    with open(fc_path, "r", encoding="utf-8") as f:
                        other_fc = json.load(f)
                    other_probs = [b.get("probability")
                                   for b in (other_fc.get("binary_forecasts") or [])
                                   if isinstance(b, dict)]
                    if len(other_probs) != len(probs):
                        continue
                    if all(abs(float(a) - float(b)) <= 0.01
                           for a, b in zip(probs, other_probs)):
                        return {
                            "issue": "binary probabilities identical to a different-simulation "
                                     "report — forecasts insensitive to simulation",
                            "other_report_id": other_id,
                            "other_simulation_id": str(meta.get("simulation_id") or ""),
                        }
                except (OSError, ValueError, TypeError):
                    continue
        except Exception:  # noqa: BLE001
            return None
        return None

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

    # RPT-5: 系统确定性注入的对比表注释 blockquote（_render_comparison_table 固定输出），
    # 永远不可能命中研究材料，必须豁免。
    _TABLE_NOTE_TEXT = "上表为确定性聚合结果，正文请围绕这些权威差值展开解读，勿自行复算或反转方向。"
    _S_CITATION_RE = re.compile(r"[\[【]\s*S\d+\s*[\]】]", re.I)

    @staticmethod
    def _norm_quote_text(text: str) -> str:
        """RPT-5: 归一化引文/底料——去 markdown 强调与引号字符（弯直混用）、压空白、小写，
        让逐字比对不被排版差异（**加粗**、弯引号、折行）制造假阳性。纯函数。"""
        import re as _re
        t = _re.sub(r"[*_`]+", "", str(text or ""))
        t = _re.sub(r"[\"'“”‘’„‟「」『』]", "", t)
        return _re.sub(r"\s+", " ", t).strip().lower()

    def _audit_quote_provenance(self, report_markdown: str) -> Dict[str, Any]:
        """QUALITY-OPT S2 + RPT-5: detect laundered quotes. A blockquote presented as a
        REAL/factual quote (not labeled as simulation) must substring-match the research
        material; otherwise it is likely sim-roleplay or a graph-edge string dressed up as
        a real source, or an outright fabrication. Returns a count + examples;
        observability only (never mutates).

        RPT-5（REPORT_QUOTE_AUDIT_V2，默认开）修掉系统性假阳性：(1) 豁免系统注入的
        大纲摘要/对比表注释 blockquote；(2) 归一化 + 头/中/尾三段探针（此前单一 40 字
        头探针对强调符/弯引号极脆）；(3) 带 [S#] 引用但非逐字的引文单列为
        cited_unverbatim（仍是缺陷，但非捏造，发布门仅对 ungrounded 降级）。"""
        import re as _re
        lines = [ln.strip()[1:].strip() for ln in (report_markdown or "").splitlines()
                 if ln.strip().startswith(">")]
        quotes = [q for q in lines if len(q) >= 12]
        if not quotes:
            return {}
        v2 = bool(getattr(Config, "REPORT_QUOTE_AUDIT_V2", True))
        ground_raw = ((self.research_report or "") + "\n" + (self._background_block or "")
                      + "\n" + (getattr(self, "situation_brief", "") or ""))
        ground = self._norm_quote_text(ground_raw) if v2 else ground_raw.lower()
        sim_labels = ("模拟", "simulation", "代理人", "推演", "sim-agent", "simulated agent")
        _summary_n = self._norm_quote_text(getattr(self, "_outline_summary", "") or "")
        _table_note_n = self._norm_quote_text(self._TABLE_NOTE_TEXT)
        ungrounded: List[str] = []
        unverbatim: List[str] = []
        for q in quotes:
            ql = q.lower()
            if any(t in ql for t in sim_labels):       # honestly labeled as simulation → fine
                continue
            if not v2:
                probe = _re.sub(r'^["“”「『\'\s]+', '', q)[:40].lower().strip()
                if probe and probe in ground:           # matches real research material → fine
                    continue
                ungrounded.append(q[:90])
                continue
            qn = self._norm_quote_text(q)
            if not qn:
                continue
            if (_summary_n and qn == _summary_n) or qn == _table_note_n:
                continue                                # 系统注入的 blockquote → 豁免
            probes = [qn[:40]]
            if len(qn) > 80:
                mid = len(qn) // 2
                probes.append(qn[mid:mid + 40])
            if len(qn) > 40:
                probes.append(qn[-40:])
            if any(p and p in ground for p in probes):  # verbatim match → fine
                continue
            if self._S_CITATION_RE.search(q):           # 有 [S#] 来源但非逐字 → 单列
                unverbatim.append(q[:90])
                continue
            ungrounded.append(q[:90])
        out = {"total_quotes": len(quotes), "ungrounded": len(ungrounded),
               "examples": ungrounded[:5]}
        if v2:
            out["cited_unverbatim"] = len(unverbatim)
            if unverbatim:
                out["unverbatim_examples"] = unverbatim[:5]
        return out

    def _audit_stat_plausibility(self, report_markdown: str) -> Dict[str, Any]:
        """QUALITY-OPT S12: flag implausible headline statistics that anchor whole reports —
        extreme year-over-year growth (>100%) like 'DRAM +303%' / '增长249.5%'. Best-effort
        prose scan; observability → forecast.quality. (Sub-market>parent & future-dated earnings
        need structured facts → handled in the research quant sanity gate.)"""
        import re as _re
        md = report_markdown or ""
        flags: List[str] = []
        pat = _re.compile(
            r'(?:\+|增长|增幅|同比|环比|growth|increase|surg\w*|yoy|year[\- ]over[\- ]year)'
            r'[^0-9%]{0,10}([0-9]{3,4}(?:\.[0-9]+)?)\s*%', _re.I)
        for m in pat.finditer(md):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if v > 100:
                ctx = md[max(0, m.start() - 24):m.start() + 14].replace("\n", " ").strip()
                flags.append(f"{v:.0f}% growth near '…{ctx}…'")
        # dedup
        seen = set(); uniq = []
        for f in flags:
            if f not in seen:
                seen.add(f); uniq.append(f)
        return {"implausible_stats": uniq[:8], "count": len(uniq)}

    def _audit_numeric_consistency(self, report_markdown: str, forecast: Dict[str, Any]) -> Dict[str, Any]:
        """QUALITY-OPT S11: flag prose scenario-probabilities that disagree with forecast.json
        by >1pt (the 42/21/15/11/11-vs-38/17/13/12/20 failure). forecast.json is the single
        source of truth; prose must match it. Observability → forecast.quality. Best-effort."""
        import re as _re
        if not isinstance(forecast, dict):
            return {}
        md = report_markdown or ""
        issues: List[str] = []
        for s in (forecast.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "").strip()
            try:
                p = round(float(s.get("probability") or 0.0) * 100)
            except (TypeError, ValueError):
                continue
            if len(name) < 4:
                continue
            idx = md.find(name)
            if idx < 0:
                continue
            window = md[max(0, idx - 18):idx + len(name) + 55]
            pcts = []
            for m in _re.finditer(r'(\d{1,3})\s*%', window):
                try:
                    pv = int(m.group(1))
                except ValueError:
                    continue
                if 0 <= pv <= 100:
                    pcts.append(pv)
            if not pcts:
                continue
            if any(abs(pv - p) <= 1 for pv in pcts):
                continue  # a nearby percentage matches forecast.json → consistent
            near = [pv for pv in pcts if abs(pv - p) <= 60]
            if near:
                pv = min(near, key=lambda x: abs(x - p))
                issues.append(f"scenario '{name[:28]}': prose {pv}% vs forecast.json {p}%")
        return {"scenario_prob_mismatches": issues[:8], "mismatch_count": len(issues)}

    def _lang_override(self) -> str:
        """QUALITY-OPT B0: a hard output-language directive prepended to every plan/section
        system prompt. It explicitly OVERRIDES the template's legacy 'mirror the source
        language / write in Chinese' rule, so an English brief yields an English submission."""
        lang = getattr(self, "output_language", None) or "English"
        return (
            "═══ ABSOLUTE OUTPUT-LANGUAGE RULE (overrides every other language instruction below) ═══\n"
            f"Write the ENTIRE report — every heading, sentence, table cell, and quotation — in {lang}. "
            f"Translate any tool / interview / source output into {lang} before using it. Never switch "
            "languages mid-report. This rule takes precedence over any instruction to mirror the source "
            "language.\n\n"
        )

    # RQ-2 语言纯度：CJK 字符类（含中日韩统一表意 + 扩展A + 假名 + 谚文）。
    _CJK_CHAR = r"一-鿿㐀-䶿぀-ヿ가-힯"
    _CJK_RUN_RE = re.compile(
        r"[" + _CJK_CHAR + r"]" + r"[" + _CJK_CHAR + r"\s，。、；：？！…（）「」『』《》%\d\.\-]*")
    # 长英文散文片段（>=40 字符且含 >=4 空格 ⇒ 5+ 词），避开品牌/型号/代号等短拉丁 token。
    _LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,.'’\-()%/&]{39,}")

    def _apply_language_purity(self, report_id: str, report: "Report") -> None:
        """RQ-2：成稿语言纯度扫描。目标语言为非 CJK（英文）时检测残留 CJK 片段，反之检测残留长
        英文散文片段；一次批量 chat_json 调用译成目标语言并逐行内联替换。引用型片段（blockquote /
        引号内）保留原文为括注。无片段或任何错误一律 degrade-safe 跳过（保留原文），并改写
        full_report.md。幂等：纯净成稿命中零片段即为 no-op。"""
        try:
            if not getattr(Config, "REPORT_LANGUAGE_PURITY", True):
                return
            llm = getattr(self, "llm", None)
            if llm is None or not hasattr(llm, "chat_json"):
                return
            md = report.markdown_content or ""
            if not md.strip():
                return
            lang = getattr(self, "output_language", None) or "English"
            target_is_cjk = not str(lang).strip().lower().startswith("en")

            # 1) 采集污染片段（跳过围栏代码块；CJK-目标时对拉丁检测额外保守）。
            segments: List[str] = []
            seen = set()
            in_fence = False
            for line in md.splitlines():
                s = line.strip()
                if s.startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence or not s:
                    continue
                if target_is_cjk:
                    # 只扫散文行：跳过标题 / 表格 / 引用块 / 含 URL 的行，降低误伤品牌/链接。
                    if (s.startswith("#") or s.startswith(">") or "|" in s
                            or "http://" in s or "https://" in s):
                        continue
                    candidates = self._LATIN_RUN_RE.findall(line)
                    candidates = [c for c in candidates if c.count(" ") >= 4]
                else:
                    candidates = self._CJK_RUN_RE.findall(line)
                for c in candidates:
                    seg = c.strip().strip("（）()「」\"'“”")
                    if len(seg) < 2:
                        continue
                    if (not target_is_cjk) and len(re.findall(r"[" + self._CJK_CHAR + r"]", seg)) < 2:
                        continue
                    seg = seg[:300]
                    if seg and seg not in seen:
                        seen.add(seg)
                        segments.append(seg)
                    if len(segments) >= 60:              # 有界：单次批量最多 60 片段
                        break
                if len(segments) >= 60:
                    break
            if not segments:
                return

            # 2) 一次批量翻译（chat_json，fast 档；失败/缺键则该片段保留原文）。
            numbered = "\n".join(f"{i}. {seg}" for i, seg in enumerate(segments, 1))
            sys_prompt = (
                f"You are a precise translator. Translate each numbered segment into {lang}. "
                "Preserve numbers, percentages, [S#] citation tags, and proper nouns verbatim. "
                'Return ONLY a JSON object mapping each segment index (as a string) to its '
                f'{lang} translation, e.g. {{"1": "...", "2": "..."}}.'
            )
            parsed = self.llm.chat_json(
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": numbered}],
                temperature=0.0, max_tokens=4096, tier="fast",
            )
            if not isinstance(parsed, dict) or not parsed:
                return
            mapping: List[Tuple[str, str]] = []
            for i, seg in enumerate(segments, 1):
                tr = parsed.get(str(i)) or parsed.get(i)
                if isinstance(tr, str):
                    tr = tr.strip()
                    if tr and tr != seg:
                        mapping.append((seg, tr))
            if not mapping:
                return
            # 长片段优先替换，避免短片段先替换切断长片段。
            mapping.sort(key=lambda kv: len(kv[0]), reverse=True)

            # 3) 逐行内联替换；引用型行（blockquote / 引号内）保留原文为括注。
            paren = (lambda t, o: f"{t}（{o}）") if target_is_cjk else (lambda t, o: f"{t} ({o})")
            in_fence = False
            replaced = 0
            out_lines: List[str] = []
            for line in md.splitlines():
                if line.strip().startswith("```"):
                    in_fence = not in_fence
                    out_lines.append(line)
                    continue
                if in_fence:
                    out_lines.append(line)
                    continue
                is_quote_line = line.lstrip().startswith(">")
                new_line = line
                for orig, tr in mapping:
                    if orig not in new_line:
                        continue
                    quoted_inline = bool(re.search(
                        r'["“”「『]\s*' + re.escape(orig) + r'\s*["”「』」]', new_line))
                    if is_quote_line or quoted_inline:
                        new_line = new_line.replace(orig, paren(tr, orig))
                    else:
                        new_line = new_line.replace(orig, tr)
                    replaced += 1
                out_lines.append(new_line)
            if not replaced:
                return
            new_md = "\n".join(out_lines)
            if new_md == md:
                return
            report.markdown_content = new_md
            try:
                folder = ReportManager._get_report_folder(report_id)
                write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
            except Exception as _we:  # noqa: BLE001
                logger.warning(f"回写语言纯度成稿 full_report.md 失败（忽略）: {_we}")
            logger.info(
                f"语言纯度扫描: {report_id} 目标语言 {lang}，内联翻译 {len(mapping)} 类片段"
                f"（{replaced} 处替换）")
        except Exception as _lpe:  # noqa: BLE001 — 纯度扫描为旁路增强，失败保留原文
            logger.warning(f"语言纯度扫描失败（忽略，保留原文）: {_lpe}")

    # ──────────────────────────────────────────────────────────────
    # BILINGUAL：自动生成成稿的另一语种版本（英⇄中），逐 H2 章节并发翻译
    # ──────────────────────────────────────────────────────────────
    # 百分比/概率 token 提取正则（数字完整性核对：原文所有此类 token 必须出现在译文中）。
    _NUMBER_INTEGRITY_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

    def _detect_translation_target(
        self, md: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """复用 detect_output_language / CJK 比率判定成稿语言，返回
        (source_code, target_code, target_language_name)。中文→英文、英文→中文；
        其它脚本（西里尔/阿拉伯…被 CJK 比率误判为 English）→ (None, None, None) 跳过。"""
        text = md or ""
        if not text.strip():
            return None, None, None
        try:
            from .requirement_spec import detect_output_language
            lang = detect_output_language(text)  # "Chinese" | "English"
        except Exception:  # noqa: BLE001 — 判定失败即保守跳过
            return None, None, None
        if lang == "Chinese":
            return "zh", "en", "professional analyst-grade English"
        # English 分支：再确认确为拉丁脚本，排除非中英文语种被 CJK 比率误判为 English。
        letters = sum(1 for ch in text if ch.isalpha())
        if letters:
            latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
            if latin / letters < 0.5:
                return None, None, None
        return "en", "zh", "简体中文（Simplified Chinese）"

    @staticmethod
    def _split_markdown_h2_sections(md: str) -> List[str]:
        """按 H2（行首 '## '，不含 '### '）边界把成稿切成若干块；首个 H2 之前的前言
        （H1 标题 + 摘要 blockquote）为块 0。跳过围栏代码块内的 '## '（避免把代码/示例里的
        井号当章节边界）。各块以 '\\n' 拼接后 == 原文（无增删换行），保证结构无损。"""
        lines = (md or "").split("\n")
        chunks: List[List[str]] = []
        cur: List[str] = []
        in_fence = False
        for ln in lines:
            s = ln.lstrip()
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = not in_fence
                cur.append(ln)
                continue
            # H2 边界：'## ' 开头但非 '### '（后者 startswith('## ') 为 False，无需额外判断）
            if (not in_fence) and ln.startswith("## "):
                if cur:
                    chunks.append(cur)
                cur = [ln]
            else:
                cur.append(ln)
        if cur:
            chunks.append(cur)
        return ["\n".join(c) for c in chunks]

    def _translate_section(self, section_md: str, target_language_name: str) -> str:
        """把单个章节（H2 块）译成目标语言，严格保留 markdown 结构、围栏、表格列数、引用标记、
        数字概率。调用失败 / 空输出 → 返回原文（degrade-safe，交由数字完整性核对标记）。"""
        if not section_md.strip():
            return section_md
        sys_prompt = (
            "You are a professional translator for institutional analytic / forecasting reports. "
            f"Translate the following Markdown into {target_language_name}. "
            "Obey EVERY rule strictly:\n"
            "1. Preserve ALL Markdown structure verbatim: heading levels (#/##/###), lists, "
            "blockquotes, bold/italic, and tables — tables MUST keep the EXACT same number of "
            "columns and the |---| separator row.\n"
            "2. Copy every fenced code block and mermaid block (``` or ~~~ fences, and everything "
            "inside them) UNCHANGED — never translate content inside fences.\n"
            "3. Keep image references, URLs, citation markers ([S1] / 【S1】 / [S?]), numbers, "
            "percentages and probabilities BYTE-IDENTICAL (e.g. '37%' stays '37%').\n"
            "4. Keep proper nouns and source names as-is; do not invent name translations. You may "
            "add a target-language rendering in parentheses only where it aids readability.\n"
            "5. Output ONLY the translated Markdown — no preamble, no commentary, and do NOT wrap "
            "the whole answer in a code fence."
        )
        # 输出预算：中文比英文更紧凑，英译中略膨胀；给宽裕上限（有界，防单章截断）。
        est = max(2048, min(16384, len(section_md) // 2 + 1024))
        try:
            out = self.llm.chat(
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": section_md}],
                temperature=0.1, max_tokens=est, tier="strong",
            )
        except Exception as _te:  # noqa: BLE001 — 单章翻译失败保留原文，不牵连整篇
            logger.warning(f"双语报告：章节翻译调用失败，保留原文: {_te}")
            return section_md
        out = (out or "").strip()
        if not out:
            return section_md
        # 模型偶把整段答案包进 ``` 围栏——仅当首行是纯 fence 标记时剥掉，避免破坏结构。
        first = out.split("\n", 1)[0].strip()
        if first in ("```", "```markdown", "```md", "~~~") and out.rstrip().endswith(("```", "~~~")):
            inner = out.split("\n", 1)[1] if "\n" in out else ""
            inner = inner.rsplit("```", 1)[0].rsplit("~~~", 1)[0]
            if inner.strip():
                out = inner.rstrip()
        return out

    def _generate_bilingual_report(self, report_id: str, report: "Report") -> None:
        """BILINGUAL：在报告最终化/可视化/纯度处理之后，自动生成成稿的另一语种版本。

        流水：① 复用 detect_output_language 判定成稿语言（英⇄中，其它脚本跳过）；② 按 H2 边界
        切块，用小 ThreadPoolExecutor（REPORT_TRANSLATION_CONCURRENCY）并发逐章翻译（严格保留
        结构/围栏/表格/数字）；③ 拼装落 reports/{id}/full_report.{en|zh}.md；④ 数字完整性核对
        （原文所有百分比/概率 token 必须出现在译文，否则 translation_quality=warning 仍发布）；
        ⑤ 把 translations 条目写入 report.translations（save_report 随后持久化进 meta.json）。

        完全 degrade-safe：绝不修改 report.markdown_content / full_report.md；任何失败仅告警。"""
        if not getattr(Config, "REPORT_BILINGUAL", True):
            return
        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "chat"):
            return
        md = report.markdown_content or ""
        if not md.strip():
            return
        src_code, tgt_code, tgt_name = self._detect_translation_target(md)
        if not tgt_code:
            logger.info(f"双语报告：成稿语言非中/英（或无法判定），跳过翻译: {report_id}")
            return

        chunks = self._split_markdown_h2_sections(md)
        if not chunks:
            return
        try:
            conc = max(1, int(getattr(Config, "REPORT_TRANSLATION_CONCURRENCY", 4) or 4))
        except (TypeError, ValueError):
            conc = 4

        translated: List[Optional[str]] = [None] * len(chunks)

        def _work(pair: Tuple[int, str]) -> Tuple[int, str]:
            i, ch = pair
            return i, self._translate_section(ch, tgt_name)

        if conc > 1 and len(chunks) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(conc, len(chunks))) as ex:
                for i, tr in ex.map(_work, list(enumerate(chunks))):
                    translated[i] = tr
        else:
            for i, ch in enumerate(chunks):
                translated[i] = self._translate_section(ch, tgt_name)

        # 逐章 strip 后以空行拼接，保证 H2 章节间有标准 markdown 空行分隔（各段已含自身标题）。
        translated_md = "\n\n".join(
            (t if t is not None else chunks[i]) for i, t in enumerate(translated)
        ).strip() + "\n"
        # no-op 守卫：译文为空、或与原文在「空白归一」意义上完全相同（如整篇翻译退化为原文——
        # 同语种 / LLM 全程降级回退原文），跳过落盘，避免生成一份与主报告无异的冗余文件。
        def _collapse(t: str) -> str:
            return re.sub(r"\s+", " ", t or "").strip()
        if not translated_md.strip() or _collapse(translated_md) == _collapse(md):
            logger.info(f"双语报告：译文为空或与原文实质相同，跳过落盘: {report_id}")
            return

        # 数字完整性核对：原文所有百分比/概率 token（归一去空格）须为译文的子集。
        def _num_tokens(t: str) -> set:
            return {m.replace(" ", "") for m in self._NUMBER_INTEGRITY_RE.findall(t or "")}
        missing = sorted(_num_tokens(md) - _num_tokens(translated_md))
        quality = "warning" if missing else "ok"
        if missing:
            logger.warning(
                f"双语报告数字完整性告警: {report_id} 译文缺失 {len(missing)} 个"
                f"百分比/概率 token: {missing[:12]}")

        # 落盘 full_report.<lang>.md（原子写入；不触碰主 full_report.md）。
        out_path = ReportManager._get_report_translation_path(report_id, tgt_code)
        write_text_atomic(out_path, translated_md)

        # 记录 translations 条目（去重同语种旧条目后追加）。model 取本次 LLM 客户端模型名。
        try:
            model_name = getattr(self.llm, "model", None) or getattr(Config, "LLM_MODEL_NAME", "")
        except Exception:  # noqa: BLE001
            model_name = getattr(Config, "LLM_MODEL_NAME", "")
        entry = {
            "lang": tgt_code,
            "source_lang": src_code,
            "path": f"full_report.{tgt_code}.md",
            "chars": len(translated_md),
            "created_at": datetime.now().isoformat(),
            "model": model_name,
            "translation_quality": quality,
            "missing_numbers": missing[:20],
        }
        existing = [
            e for e in (report.translations or [])
            if not (isinstance(e, dict) and e.get("lang") == tgt_code)
        ]
        existing.append(entry)
        report.translations = existing
        logger.info(
            f"双语报告已生成: {report_id} {src_code}→{tgt_code}，{len(chunks)} 章，"
            f"{len(translated_md)} 字，quality={quality}")

    def _prepend_binary_forecasts_section(self, report_id: str, report: "Report") -> None:
        """QUALITY-OPT B1: insert the deterministic Part-1 binary-forecast table right after
        the report's H1 title, so the brief's headline deliverable leads the document and its
        numbers are guaranteed to match forecast.json. No-op if no binaries. Rewrites full_report.md.

        PM-2：紧随二元表插入确定性「### Market Cross-Check」块（预测 vs 市场隐含概率对照 +
        >10pp 判定 + 未匹配市场清单），数据取 forecast['binary_forecasts'][].market_anchor /
        market_comparison 与研究期快照 self._prediction_markets。无锚定/无未匹配市场 → 不追加。
        与二元表同处一次插入，受同一 Part-1 标记幂等门保护（重最终化绝不二次插入，H3 标记不干扰
        三部骨架对首个 "## " 详细章节的定位）。
        """
        from .forecast_extractor import render_binary_forecasts_block
        fc = self._forecast_spine or {}
        if not (fc.get("binary_forecasts")):
            return
        _lang = (getattr(self, "output_language", None)
                 or getattr(Config, "DEERFLOW_RESEARCH_LANGUAGE", None) or "English")
        block = render_binary_forecasts_block(fc, language=_lang)
        if not block:
            return
        # PM-2：确定性市场交叉核对块，紧随二元表。degrade-safe：渲染失败/空 → 不追加。
        try:
            xcheck = render_market_comparison_block(
                fc, markets=getattr(self, "_prediction_markets", None), lang=_lang)
        except Exception as _xe:  # noqa: BLE001 — 对照块为增强，失败不影响二元表前置
            logger.warning(f"渲染 Market Cross-Check 块失败（忽略）: {_xe}")
            xcheck = ""
        if xcheck and not any(m in block for m in _MARKET_XCHECK_MARKERS):
            block = block + "\n\n" + xcheck
        md = report.markdown_content or ""
        if "Part 1 — Binary Forecasts" in md or "第一部分 · 二元预测" in md:
            return  # idempotent — never double-insert on re-finalize
        lines = md.split("\n", 1)
        if lines and lines[0].lstrip().startswith("# "):
            # after the H1 title (+ any immediate blockquote summary stays below Part 1)
            new_md = lines[0] + "\n\n" + block + "\n\n" + (lines[1] if len(lines) > 1 else "")
        else:
            new_md = block + "\n\n" + md
        report.markdown_content = new_md
        try:
            folder = ReportManager._get_report_folder(report_id)
            write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
        except Exception as _we:  # noqa: BLE001
            logger.warning(f"重写 full_report.md（前置二元预测章节）失败（忽略）: {_we}")
        logger.info(f"已前置 Part-1 二元预测章节: {report_id} "
                    f"({len(fc.get('binary_forecasts') or [])} 条)")

    # ──────────────────────────────────────────────────────────────
    # B2: 三部结构骨架 — Part 1（二元预测）→ Part 2（框架综合）→ Part 3（附录详析）
    # ──────────────────────────────────────────────────────────────
    # 幂等判定 + 定位用标记（与 _prepend_binary_forecasts_section / 渲染器的标题一致）。
    _PART1_MARKERS = ("Part 1 — Binary Forecasts", "第一部分 · 二元预测")
    _PART2_MARKERS = ("Part 2 — Framework & Synthesis", "第二部分 · 框架与综合")

    # VIZ-1 钩子：manifest 的 placement_hint → 章节标题匹配关键词（中英并列）。Mermaid 图按此
    # 把图就地插到最相关的详细章节标题后；无命中 → 归入文末「Visual Annex / 可视化附录」。
    _VIZ_PLACEMENT_KEYWORDS = {
        "timeline": ("timeline", "chronology", "时间线", "时间轴", "事件脉络", "沿革"),
        "actors": ("actor", "stakeholder", "coalition", "faction", "player",
                   "角色", "参与者", "行动者", "派系", "阵营", "利益相关"),
        "drivers": ("driver", "causal", "mechanism", "dynamic", "catalyst",
                    "驱动", "因果", "机制", "动力", "催化"),
        "scenarios": ("scenario", "情景", "情境", "剧本", "路径"),
        "binary_forecasts": ("forecast", "probability", "market", "prediction",
                             "预测", "概率", "市场", "赔率"),
        "simulation": ("simulation", "trajectory", "model", "outcome", "world",
                       "模拟", "推演", "轨迹", "世界态", "结果分布", "建模"),
        "comparison": ("comparison", "baseline", "counterfactual", "contrast",
                       "对比", "基线", "反事实", "对照", "情景差"),
        "calibration": ("calibration", "reliability", "校准", "可靠", "信度"),
    }

    def _binary_min_count(self) -> int:
        """B2: 二元预测最小数量 = max(需求书解析出的 binary_min_count, Config 默认)。

        需求书写明「15+ binary forecasts」时不能被 Config 的 10 静默压低；spec 未解析出
        数量/解析失败时沿用 Config（degrade-safe，与历史行为一致）。"""
        try:
            n = int(getattr(Config, "BINARY_FORECASTS_MIN_COUNT", 10) or 10)
        except (TypeError, ValueError):
            n = 10
        try:
            from .requirement_spec import parse_requirement_spec
            spec_n = parse_requirement_spec(
                getattr(self, "simulation_requirement", "") or "",
                getattr(self, "research_report", "") or "",
            ).get("binary_min_count")
            if spec_n:
                n = max(n, int(spec_n))
        except Exception:  # noqa: BLE001 — spec 解析失败沿用 Config
            pass
        return n

    def _section_key_points(self, report_markdown: str, max_sections: int = 8,
                            per_section_chars: int = 350, max_chars: int = 4000) -> str:
        """B2: 确定性截取各详细章节的「要点」（标题 + 开头片段）供 Part 2 综合，
        有界回灌避免整稿吃进提示词。跳过 Part 1 表格块；无章节返回 ""。"""
        lines = (report_markdown or "").split("\n")
        sections: List[tuple] = []  # (title, body_lines)
        cur_title = None
        cur_body: List[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                if cur_title is not None:
                    sections.append((cur_title, cur_body))
                title = s[3:].strip()
                cur_title = None if any(m in title for m in self._PART1_MARKERS) else title
                cur_body = []
            elif cur_title is not None:
                cur_body.append(ln)
        if cur_title is not None:
            sections.append((cur_title, cur_body))
        segs: List[str] = []
        for title, body in sections[:max_sections]:
            text = " ".join(x.strip() for x in body if x.strip())[:per_section_chars]
            segs.append(f"- {title}: {text}")
        out = "\n".join(segs)
        return out[:max_chars]

    def _build_part2_synthesis(self, report_markdown: str) -> str:
        """B2: 一次 LLM 调用生成 Part 2「框架与综合」正文——从预测骨架 + 各章要点 +
        预测市场信号包做紧凑综合（RQ-1：默认 ≤~2800 词；需求书解析出 page_budget 时按
        ~150 词/页折算收紧，下限 600 词）。失败/过短返回 ""（调用方跳过，绝不写占位符）。"""
        lang = getattr(self, "output_language", None) or "English"
        cap_words = 2800
        try:
            from .requirement_spec import parse_requirement_spec
            _pb = parse_requirement_spec(
                getattr(self, "simulation_requirement", "") or "",
                getattr(self, "research_report", "") or "",
            ).get("page_budget")
            if _pb:
                cap_words = max(600, min(2800, int(_pb) * 150))
        except Exception:  # noqa: BLE001 — spec 解析失败用默认词数
            pass
        from .forecast_extractor import render_forecast_spine_block
        try:
            spine_block = (render_forecast_spine_block(self._forecast_spine)
                           if getattr(self, "_forecast_spine", None) else "")
        except Exception:  # noqa: BLE001
            spine_block = ""
        parts: List[str] = []
        if spine_block:
            parts.append("[Forecast spine]\n" + spine_block[:3000])
        mp = getattr(self, "_market_pack", "")
        if mp:
            # PM-2：Part-2 综合注入完整市场包（此前 [:2500] 截断会丢掉尾部低成交量但高相关的市场）。
            parts.append("[Prediction market signals]\n" + str(mp))
        key_points = self._section_key_points(report_markdown)
        if key_points:
            parts.append("[Section key points]\n" + key_points)
        if not parts:
            return ""  # 没有任何可综合的输入 → 跳过（绝不让模型凭空写）
        prompt = (
            "You are the lead forecaster assembling 'Part 2 — Framework & Synthesis' of a "
            "three-part forecast submission (Part 1 = the binary-forecast table, Part 3 = the "
            "detailed appendix).\n"
            f"Write a TIGHT synthesis of AT MOST {cap_words} words in {lang}: the analytical "
            "framework, the causal logic connecting the key drivers to the Part-1 probabilities, "
            "the decisive evidence, and how the prediction-market anchors (when present) were "
            "weighed. DEFEND the spine's probabilities — do not introduce numbers that contradict "
            "them. Use short paragraphs and at most '###' sub-headings; do NOT emit a '## Part 2' "
            "top-level heading (the system adds it); no placeholders or meta commentary.\n\n"
            + "\n\n".join(parts)
        )
        text = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=8192,  # RQ-1: 4096→8192，容纳 ~2800 词综合
        )
        text = str(text or "").strip()
        # 剥掉模型可能自带的 Part 2 大标题（系统会加），避免双标题。
        text = re.sub(r"^#{1,2}\s*(?:Part\s*2|第二部分)[^\n]*\n+", "", text, count=1).strip()
        return text if len(text) >= 200 else ""

    def _apply_three_part_skeleton(self, report_id: str, report: "Report") -> None:
        """B2: 把成稿重排为三部结构——在已前置的 Part 1 二元预测表之后插入
        「Part 2 — Framework & Synthesis」（一次 LLM 紧凑综合），随后以
        「Part 3 — Appendix: Detailed Analysis」标题包住既有详细章节（纯结构性重标签，
        不动章节内容）。仅在 Part 1 已插入时生效；幂等（已有 Part 2 标记即跳过）；
        综合失败/过短 → 一行告警并整体跳过（绝不写占位符，成稿保持原结构）。
        """
        md = report.markdown_content or ""
        if not md or not any(m in md for m in self._PART1_MARKERS):
            return  # 无 Part 1（无二元预测）→ 不套三部结构
        if any(m in md for m in self._PART2_MARKERS):
            return  # 幂等 — 重复最终化时绝不二次插入
        synthesis = self._build_part2_synthesis(md)
        if not synthesis:
            logger.warning("Part 2 综合生成失败/为空，跳过三部结构骨架（保留原结构，不写占位符）")
            return
        zh = not str(getattr(self, "output_language", "") or "English").lower().startswith("en")
        if zh:
            part2_head = "## 第二部分 · 框架与综合（Part 2 — Framework & Synthesis）"
            part3_head = "## 第三部分 · 附录：详细分析（Part 3 — Appendix: Detailed Analysis）"
            part3_note = "_以下为支撑第一、二部分的逐章详细分析。_"
        else:
            part2_head = "## Part 2 — Framework & Synthesis"
            part3_head = "## Part 3 — Appendix: Detailed Analysis"
            part3_note = "_The detailed, section-by-section analysis supporting Parts 1–2 follows._"
        lines = md.split("\n")
        # 定位第一个非 Part-1 的 "## " 标题 = 详细章节起点；Part 2 + Part 3 标题插在其前。
        insert_at = None
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("## ") and not any(m in s for m in self._PART1_MARKERS):
                insert_at = i
                break
        block = [part2_head, "", synthesis, "", part3_head, "", part3_note, ""]
        if insert_at is None:
            new_lines = lines + [""] + block  # 无详细章节（边界）：附在末尾，Part 3 空壳仍标出
        else:
            new_lines = lines[:insert_at] + block + lines[insert_at:]
        new_md = "\n".join(new_lines)
        report.markdown_content = new_md
        try:
            folder = ReportManager._get_report_folder(report_id)
            write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
        except Exception as _we:  # noqa: BLE001
            logger.warning(f"重写 full_report.md（三部结构骨架）失败（忽略）: {_we}")
        logger.info(f"已套用三部结构骨架（Part 2 综合 {len(synthesis)} 字）: {report_id}")

    # ──────────────────────────────────────────────────────────────
    # VIZ-1 钩子：确定性可视化注入（build_all → 图表落盘 → 注入成稿）
    # ──────────────────────────────────────────────────────────────
    def _inject_visualizations(self, report_id: str, report: "Report") -> None:
        """VIZ-1 钩子：调用 ReportVisualizer.build_all 由已落盘的结构化工件（forecast/comparison/
        actors/world_state_trajectory/timeline）确定性生成图表（charts/*.mmd + *.png +
        viz_manifest.json），再把图注入成稿——Mermaid 块按章节标题/关键词模糊匹配就地插入，
        未匹配的图（含全部 PNG）汇入文末「## Visual Annex / 可视化附录」（双语随报告语言）。
        PNG 用相对路径 charts/xxx.png（Web 经 /charts 端点、PDF 经绝对化重写都成立）。

        幂等：每个图带唯一 HTML 注释标记（<!-- viz:charts/xxx --> ），重最终化命中标记即跳过。
        与 _prepend_binary_forecasts_section 同族的「marker 幂等 + 原子重写」模式；任何失败降级为
        一行告警 + 不改成稿（图仍已落盘，degrade-safe）。调用点在 Part-1 前置之后、三部骨架之前。
        """
        if not getattr(Config, "REPORT_VISUALIZATIONS", True):
            return
        md = report.markdown_content or ""
        if not md:
            return
        try:
            from .report_visualizer import ReportVisualizer
        except Exception as _ie:  # noqa: BLE001 — 可视化器不可用即跳过（degrade-safe）
            logger.warning(f"可视化器不可用，跳过图表注入（忽略）: {_ie}")
            return
        folder = ReportManager._get_report_folder(report_id)
        artifacts = self._collect_viz_artifacts(report_id, folder)
        try:
            manifest = ReportVisualizer().build_all(report_id, folder, artifacts)
        except Exception as _be:  # noqa: BLE001 — 生成失败不影响主报告
            logger.warning(f"ReportVisualizer.build_all 失败（忽略，成稿不含图区）: {_be}")
            return
        if not manifest:
            return
        try:
            new_md = self._place_visualizations(md, folder, manifest)
        except Exception as _pe:  # noqa: BLE001 — 注入失败保留原文
            logger.warning(f"图表注入成稿失败（忽略，保留原文）: {_pe}")
            return
        if new_md == md:
            return  # 幂等 no-op（全部图已注入或无可注入项）
        report.markdown_content = new_md
        try:
            write_text_atomic(os.path.join(folder, "full_report.md"), new_md)
        except Exception as _we:  # noqa: BLE001
            logger.warning(f"回写注入图表后 full_report.md 失败（忽略）: {_we}")
        logger.info(f"已注入 {len(manifest)} 项报告可视化: {report_id}")

    def _collect_viz_artifacts(self, report_id: str, folder: str) -> Dict[str, Any]:
        """汇集 build_all 消费的结构化工件（全部可选，缺失即跳过对应图）。来源皆为报告代理已有
        路径：报告文件夹的 forecast.json / comparison.json / market_comparison.json、内存
        self.actors、模拟目录的 world_state_trajectory.json、研究 handoff 的 timeline.json。"""
        def _rj(path: str) -> Optional[Any]:
            try:
                if path and os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:  # noqa: BLE001
                return None
            return None

        arts: Dict[str, Any] = {}
        # forecast.json（scenarios / binary_forecasts / 内嵌 market_comparison）——文件优先，
        # 缺失回退内存骨架。
        forecast = _rj(os.path.join(folder, "forecast.json"))
        if not isinstance(forecast, dict):
            forecast = self._forecast_spine if isinstance(self._forecast_spine, dict) else None
        if isinstance(forecast, dict):
            # PM-2：market_comparison 优先取 forecast 内嵌；缺失时补读独立 market_comparison.json。
            if not forecast.get("market_comparison"):
                mc = _rj(os.path.join(folder, "market_comparison.json"))
                if mc:
                    forecast = dict(forecast)
                    forecast["market_comparison"] = mc
            arts["forecast"] = forecast
        comp = _rj(os.path.join(folder, "comparison.json"))
        if comp:
            arts["comparison"] = comp
        if isinstance(self.actors, dict):
            arts["actors"] = self.actors
        # world_state_trajectory.json（决策通道产物，模拟数据目录）
        try:
            wst_path = os.path.join(
                getattr(Config, "OASIS_SIMULATION_DATA_DIR", "") or "",
                str(self.simulation_id or ""), "world_state_trajectory.json")
            wst = _rj(wst_path)
            if wst:
                arts["world_state_trajectory"] = wst
        except Exception:  # noqa: BLE001
            pass
        # timeline.json（研究 handoff；best-effort，找不到即不画时间线）
        tl = self._locate_timeline()
        if tl:
            arts["timeline"] = tl
        # PM-6 修复：把研究 handoff 目录钉进工件，供 ReportVisualizer._load_price_history 定位
        # handoff/market_price_history.json（bridge 在研究期落盘的 {market_id:[{t,p}]}）。此前
        # _collect_viz_artifacts 从不提供 handoff_dir/market_price_history，且无人把该文件拷进
        # reports/{id}/，故 PM-6 市场价格历史折线族一直拿不到数据、整族静默跳过（永不出图）。
        # 缺失/失败即不设该键（degrade-safe，价格历史图仍自动跳过，行为不变）。
        hd = self._locate_handoff_dir()
        if hd:
            arts["handoff_dir"] = hd
        return arts

    def _locate_handoff_dir(self) -> Optional[str]:
        """best-effort 定位本报告对应管线的研究 handoff 目录（按 simulation_id 匹配，与
        _locate_timeline 同模式）。供 PM-6 市场价格历史折线定位 handoff/market_price_history.json。
        找不到/任何失败 → None（degrade-safe）。延迟导入避免与 pipeline_orchestrator 的循环依赖。"""
        try:
            from .pipeline_orchestrator import PipelineManager
            for entry in PipelineManager.list_pipelines():
                pid = entry.get("pipeline_id")
                if not pid:
                    continue
                data = PipelineManager.load(pid)
                if not data or data.get("simulation_id") != self.simulation_id:
                    continue
                hd = data.get("handoff_dir") or PipelineManager.handoff_dir(pid)
                return hd or None
        except Exception:  # noqa: BLE001
            return None
        return None

    def _locate_timeline(self) -> Optional[Any]:
        """best-effort 定位研究 handoff 的 timeline.json（经 PipelineManager 按 simulation_id
        匹配管线，与 load_research_dossier_for_simulation 同模式）。找不到/任何失败 → None
        （degrade-safe，时间线图自动跳过）。延迟导入避免与 pipeline_orchestrator 的循环依赖。"""
        try:
            from .pipeline_orchestrator import PipelineManager
            for entry in PipelineManager.list_pipelines():
                pid = entry.get("pipeline_id")
                if not pid:
                    continue
                data = PipelineManager.load(pid)
                if not data or data.get("simulation_id") != self.simulation_id:
                    continue
                hd = data.get("handoff_dir") or PipelineManager.handoff_dir(pid)
                path = os.path.join(hd or "", "timeline.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                return None
        except Exception:  # noqa: BLE001
            return None
        return None

    def _place_visualizations(self, md: str, folder: str,
                              manifest: List[Dict[str, str]]) -> str:
        """把 manifest 的图注入成稿：Mermaid 块按 placement_hint 关键词匹配到最近详细章节标题后
        就地插入；未匹配的 Mermaid + 全部 PNG 汇入文末「Visual Annex」。逐图带唯一标记 → 幂等
        （已注入的图跳过）。无可注入项 → 原样返回 md。"""
        zh = not str(getattr(self, "output_language", "") or "English").lower().startswith("en")
        lines = md.split("\n")
        # 预扫描 "## " 详细章节标题（行号 + 小写标题），供 Mermaid 就地放置的模糊匹配。
        headings: List[Tuple[int, str]] = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("## "):
                headings.append((i, s[3:].strip().lower()))
        inserts: Dict[int, List[str]] = {}   # 标题行号 → [图 markdown 块, ...]
        annex: List[str] = []
        for item in manifest:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            marker = f"<!-- viz:{path} -->"
            if marker in md:
                continue  # 幂等：该图已注入
            vtype = str(item.get("type") or "").strip().lower()
            caption = str(item.get("caption") or "").strip()
            hint = str(item.get("placement_hint") or item.get("hint") or "").strip().lower()
            block = self._render_viz_block(folder, path, vtype, caption, marker, zh)
            if not block:
                continue
            target = self._match_section(headings, hint) if vtype == "mermaid" else None
            if target is not None:
                inserts.setdefault(target, []).append(block)
            else:
                annex.append(block)
        if not inserts and not annex:
            return md
        # 就地插入（从后往前，避免行号漂移）。插入点=标题行之后、跳过其后紧邻的空行。
        for line_no in sorted(inserts.keys(), reverse=True):
            at = line_no + 1
            while at < len(lines) and not lines[at].strip():
                at += 1
            payload: List[str] = []
            for blk in inserts[line_no]:
                payload.extend(["", blk, ""])
            lines[at:at] = payload
        new_md = "\n".join(lines)
        if annex:
            head = "## 可视化附录（Visual Annex）" if zh else "## Visual Annex"
            note = ("_以下为支撑本报告的确定性可视化，均自结构化工件生成。_" if zh else
                    "_Deterministic visualizations supporting this report, generated from "
                    "structured artifacts._")
            body = "\n\n".join(annex)
            # 附录标题已存在（重最终化残留）→ 仅追加未注入图（每图受自身标记幂等保护）。
            if head not in new_md:
                new_md = new_md.rstrip() + "\n\n" + head + "\n\n" + note + "\n\n" + body + "\n"
            else:
                new_md = new_md.rstrip() + "\n\n" + body + "\n"
        return new_md

    @staticmethod
    def _render_viz_block(folder: str, path: str, vtype: str,
                          caption: str, marker: str, zh: bool) -> str:
        """把单个 manifest 图渲染成 markdown 块：Mermaid 读 charts/*.mmd 内联其 ```mermaid 围栏；
        PNG 用相对图片语法（charts/xxx.png）。每块以唯一 HTML 注释标记打头（幂等定位）。
        读不到/空/未知类型 → ''。"""
        cap = caption or ("图示" if zh else "Figure")
        if vtype == "mermaid":
            try:
                with open(os.path.join(folder, path), "r", encoding="utf-8") as f:
                    code = f.read().strip()
            except Exception:  # noqa: BLE001
                return ""
            if not code:
                return ""
            return f"{marker}\n**{cap}**\n\n{code}"
        if vtype == "png":
            return f"{marker}\n![{cap}]({path})\n\n*{cap}*"
        return ""

    @classmethod
    def _match_section(cls, headings: List[Tuple[int, str]], hint: str) -> Optional[int]:
        """按 placement_hint 的关键词在详细章节标题里模糊匹配，返回首个命中的标题行号；
        hint 未知或无命中 → None（该图归附录）。"""
        if not headings or not hint:
            return None
        keys = cls._VIZ_PLACEMENT_KEYWORDS.get(hint)
        if not keys:
            return None
        for line_no, title in headings:
            for k in keys:
                if k and k in title:
                    return line_no
        return None

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
            # QUALITY-OPT: fold the binary-forecast conviction/objectivity gate (A3/A4) +
            # the S2/S11/S12 audits into the publish gate so they actually demote confidence.
            bq = forecast.get("binary_quality") or {}
            if bq and not bq.get("passed", True):
                issues.append("二元预测信心/客观性门未过：" + "；".join((bq.get("issues") or [])[:2]))
            _q0 = forecast.get("quality")
            _existing_q = _q0 if isinstance(_q0, dict) else {}
            if (_existing_q.get("quote_provenance") or {}).get("ungrounded"):
                issues.append(f"{_existing_q['quote_provenance']['ungrounded']} 条疑似嫁接/捏造引用 (S2)")
            if (_existing_q.get("numeric_consistency") or {}).get("mismatch_count"):
                issues.append(f"{_existing_q['numeric_consistency']['mismatch_count']} 处正文概率与 forecast.json 不符 (S11)")
            if (_existing_q.get("implausible_stats") or {}).get("count"):
                issues.append(f"{_existing_q['implausible_stats']['count']} 处疑似不合理极端增长率 (S12)")
            # MERGE into quality (do NOT overwrite the audit findings stored earlier).
            quality = dict(_existing_q)
            quality.update({
                "citation_coverage": round(coverage, 3),
                "probability_sum": prob_sum,
                "has_residual_scenario": has_residual,
                "max_probability": round(top, 3),
                "issues": issues,
                "passed": not issues,
            })
            forecast["quality"] = quality
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
                # RPT-7: 工具已因 OASIS 环境离线被移除时短路，避免未经校验的调用方
                # （原生路径等）白烧一次注定超时的 IPC 采访。
                if "interview_agents" not in self.tools:
                    return "（interview_agents 不可用：OASIS 模拟环境未在线，请改用其它检索工具）"
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
                # XRUN-5: 模型未给 query 时回退紧凑查询而非整段需求书（会被钳制到只剩引言）。
                query = parameters.get("query") or self._compact_retrieval_query()
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
                # RPT-7: 提示可用工具时列出 live 工具集（此前硬编码 3 个，遗漏了
                # simulation_outcomes/coalition_map/opinion_shift/trace_cascade 等）。
                return f"未知工具: {tool_name}。请使用以下工具之一: {', '.join(sorted(self.tools.keys()))}"
                
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
                if not isinstance(call_data, dict):
                    continue
                # RPT-7: 统一键名（tool→name / params→parameters），但不在解析层丢弃未知
                # 工具名——由 ReACT 循环给出纠正性 Observation（且不计入工具调用预算）。
                if "tool" in call_data and "name" not in call_data:
                    call_data["name"] = call_data.pop("tool")
                if "params" in call_data and "parameters" not in call_data:
                    call_data["parameters"] = call_data.pop("params")
                if not call_data.get("name"):
                    continue
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

    # RPT-7: 工具使用建议改为按 live 工具集动态渲染——interview_agents 被移除（OASIS 离线）
    # 后不得继续在每章提示词中宣传它（否则模型被 nudge 去调不存在的工具，白烧一轮预算）。
    # 前四条文案与历史静态块逐字一致；条件工具（faction_brief/scenario_diff 等）仅在存在时列出。
    _TOOL_HINT_SUMMARIES = {
        "insight_forge": "深度洞察分析，自动分解问题并多维度检索事实和关系",
        "panorama_search": "广角全景搜索，了解事件全貌、时间线和演变过程",
        "quick_search": "快速验证某个具体信息点",
        "interview_agents": "采访模拟Agent，获取不同角色的第一人称观点和真实反应",
        "simulation_outcomes": "模拟量化结果：最活跃 Agent、逐轮动作量、动作类型分布",
        "coalition_map": "派系/联盟结构（对相同对象互动的 Agent 聚类，确定性）",
        "opinion_shift": "单个 Agent 的逐轮行为轨迹（立场/参与度演变）",
        "trace_cascade": "图谱多跳传导/级联追踪（哪个节点一动就翻盘）",
        "faction_brief": "图谱社区检测派系简报（阵营/对立结构）",
        "scenario_diff": "基线 vs 情景反事实结构化对比",
    }

    def _tool_usage_hints(self) -> str:
        """RPT-7: 从 live self.tools 渲染工具使用建议 bullets（工具被移除即不再出现）。"""
        lines = [f"- {name}: {self._TOOL_HINT_SUMMARIES[name]}"
                 for name in self.tools if name in self._TOOL_HINT_SUMMARIES]
        return "\n".join(lines) if lines else "（按上方工具描述使用）"
    
    def plan_outline(
        self,
        progress_callback: Optional[Callable] = None,
        forecast_spine_block: str = "",
        require_forecast_structure: bool = False,
    ) -> ReportOutline:
        """
        规划报告大纲

        使用LLM分析模拟需求，规划报告的目录结构

        Args:
            progress_callback: 进度回调函数
            forecast_spine_block: R2-DETAIL-2 预测骨架块（先于大纲推导）。非空时钉入大纲提示词，
                让章节围绕可证伪的预测组织而非反向从叙事抽取。缺省空串 → 行为与历史一致。
            require_forecast_structure: R2-DETAIL-2 为真时强制大纲覆盖「预测框架/逐情景预测/校准与
                信心」三类章节。缺省 False → 不追加该指令，提示词与历史逐字节一致。

        Returns:
            ReportOutline: 报告大纲
        """
        logger.info("开始规划报告大纲...")
        
        if progress_callback:
            progress_callback("planning", 0, "正在分析模拟需求...")
        
        # 首先获取模拟上下文（XRUN-5: 检索用紧凑查询，需求书仍原样传给上下文回显；
        # 紧凑查询与需求书相同时不传该参数，调用形态与历史逐字节一致）
        _ctx_kwargs: Dict[str, Any] = {
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
        }
        _sq = self._compact_retrieval_query()
        if _sq and _sq != self.simulation_requirement:
            _ctx_kwargs["search_query"] = _sq
        context = self.zep_tools.get_simulation_context(**_ctx_kwargs)
        
        if progress_callback:
            progress_callback("planning", 30, "正在生成报告大纲...")
        
        system_prompt = self._lang_override() + PLAN_SYSTEM_PROMPT
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
            # XRUN-5: 全局扫描的 query 用紧凑查询（整段需求书会被钳制到只剩引言句）。
            forge = self.zep_tools.insight_forge(
                graph_id=self.graph_id,
                query=self._compact_retrieval_query(),
                simulation_requirement=self.simulation_requirement,
                report_context="报告大纲规划阶段的全局扫描",
            )
            forge_text = forge.to_text() if hasattr(forge, "to_text") else str(forge)
            if forge_text:
                sweeps.append("【图谱深挖摘要】\n" + forge_text[:6000])  # RQ-4: 3000→6000
        except Exception as e:
            logger.warning(f"plan_outline insight_forge 扫描失败（忽略）: {e}")
        try:
            outcomes = self.zep_tools.simulation_outcomes(self.simulation_id, top_n=10)
            if outcomes:
                sweeps.append("【模拟量化结果摘要】\n" + outcomes[:5000])  # RQ-4: 2500→5000
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

        # R2-DETAIL-2: 把先于大纲推导出的预测骨架钉入提示词，并（在有骨架时）强制大纲围绕预测组织。
        # forecast_spine_block 为空 / require_forecast_structure 为 False 时本段为 no-op（提示词与历史一致）。
        if forecast_spine_block:
            user_prompt += "\n\n" + forecast_spine_block
        if require_forecast_structure:
            user_prompt += (
                "\n\n**结构强制要求（预测优先）**：本报告以上述预测骨架为核心，章节须围绕预测组织。"
                "大纲必须显式覆盖以下三类章节："
                "(1) 一节阐述「预测框架与方法」——研究证据基础、情景如何划分与定价（概率来源）；"
                "(2) 围绕各核心情景的「逐情景预测」章节，逐一论证其概率、关键驱动与判定/证伪标准；"
                "(3) 一节「校准与信心」——讨论不确定性、置信区间，以及模型与模拟之间的分歧。"
                "其余章节可据预测发现自由设计，但上述三类必须被覆盖（标题可自拟，语义需对应）。"
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

            # RQ-1(4): 钳制到本次报告形状的章节数区间（PLAN_SYSTEM_PROMPT 的硬约束）：不足补齐，
            # 超出截断。小 page_budget → 5-8 节（紧凑），无/大 page_budget → 6-14 节（展开）。
            # LLM 偶尔会无视数量要求，这里兜底以保证下游章节生成数量稳定。
            _shape = self._report_shape()
            _min_sections = _shape["min_sections"]
            _max_sections = _shape["max_sections"]
            if len(sections) < _min_sections:
                _existing = {s.title for s in sections}
                for _title in self._FALLBACK_SECTION_TITLES:
                    if len(sections) >= _min_sections:
                        break
                    if _title in _existing:
                        continue
                    sections.append(ReportSection(title=_title))
                    _existing.add(_title)
                logger.warning(
                    f"大纲章节数不足 {_min_sections}，已补齐至 {len(sections)} 节"
                )
            elif len(sections) > _max_sections:
                logger.warning(
                    f"大纲章节数 {len(sections)} 超过上限 {_max_sections}，已截断"
                )
                sections = sections[:_max_sections]

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
            # RPT-2(a): 记录「大纲 LLM 调用失败、已降级为默认大纲」——这是系统性 LLM 故障的
            # 前哨信号；generate_report 据此在前两章接连失败时快速中止，而非对着死掉的
            # 提供方烧完全部章节再假装 completed。
            self._outline_degraded = True
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
                return idx, self._generate_section_with_retry(
                    section=section, outline=outline, previous_sections=[brief],
                    progress_callback=_noop, section_index=idx + 1)
            except Exception as e:  # noqa: BLE001 — per-section isolation
                logger.error(f"并发章节生成异常（降级占位符）: {section.title} -> {e}")
                return idx, SECTION_FAILURE_PLACEHOLDER

        if body:
            # RPT-9: ThreadPoolExecutor worker 线程不继承 ContextVar，_current_report_id
            # 在 worker 中为 None → _ReportIdMatchFilter 丢弃全部章节内日志（console_log.txt
            # 与 UI 日志流在并发模式下整段空白）。为每个任务复制当前上下文再运行。
            _parent_ctx = contextvars.copy_context()
            with _cf.ThreadPoolExecutor(max_workers=min(concurrency, len(body))) as ex:
                futures = [ex.submit(_parent_ctx.copy().run, _gen_body, i, s) for i, s in body]
                for fut in _cf.as_completed(futures):
                    idx, content = fut.result()
                    contents[idx] = content

        # tail sections: sequential, full body text as context for narrative closure
        body_text = [f"## {sections[i].title}\n\n{contents.get(i, '')}" for i, _ in body]
        for idx, section in tail:
            try:
                contents[idx] = self._generate_section_with_retry(
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

    def _generate_section_with_retry(
        self,
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0,
    ) -> str:
        """RPT-2: 章节异常先退避重试，再落占位符。瞬时故障（限流/超时/单次 5xx）不应
        直接把章节钉死成占位符。REPORT_SECTION_RETRY_MAX=0 时与直接调用逐字节等价。"""
        try:
            retries = int(getattr(Config, "REPORT_SECTION_RETRY_MAX", 1) or 0)
        except (TypeError, ValueError):
            retries = 1
        try:
            backoff = float(getattr(Config, "REPORT_SECTION_RETRY_BACKOFF_S", 8.0) or 0.0)
        except (TypeError, ValueError):
            backoff = 8.0
        attempt = 0
        while True:
            try:
                content = self._generate_section(
                    section, outline, previous_sections, progress_callback, section_index
                )
                break
            except Exception as e:  # noqa: BLE001 — 重试耗尽后重抛，由调用方落占位符
                if attempt >= retries:
                    raise
                attempt += 1
                wait_s = min(backoff * attempt, 60.0)
                logger.warning(
                    f"章节 {section.title} 生成异常，{wait_s:.0f}s 后重试 "
                    f"{attempt}/{retries}: {e}"
                )
                time.sleep(wait_s)
        # RQ-5：草稿通过基本有效性后，做一次廉价批判 + 至多一次修订（全程 degrade-safe，
        # 失败/关闭一律返回原草稿）。留在本方法内 ⇒ 仍处于 telemetry/并发包装之内。
        return self._reflect_and_maybe_revise_section(
            section, outline, content, previous_sections, section_index
        )

    def _reflect_and_maybe_revise_section(
        self,
        section: "ReportSection",
        outline: "ReportOutline",
        content: str,
        previous_sections: List[str],
        section_index: int = 0,
    ) -> str:
        """RQ-5：对通过基本有效性的章节草稿做一次廉价批判；未通过则至多一次修订。

        评分维度：(1) 骨架情景概率一致性；(2) 硬数字接地（带 [S#] 或与信号包一致）；
        (3) 篇幅下限；(4) 不复述前序章节。批判返回 PASS 或单条修订指令；至多一次修订抽取。
        由 self.MAX_REFLECTION_ROUNDS 上限（<=0 关闭），并受 Config.REPORT_SECTION_REFLECTION
        总开关约束。全部包在 try 里——反思是旁路增强，任何异常/缺依赖都回退原草稿。"""
        try:
            if not getattr(Config, "REPORT_SECTION_REFLECTION", True):
                return content
            cap = int(getattr(self, "MAX_REFLECTION_ROUNDS", 0) or 0)
            if cap <= 0:                                  # MAX_REFLECTION_ROUNDS 上限（0=关）
                return content
            llm = getattr(self, "llm", None)
            if llm is None or not hasattr(llm, "chat"):   # 离线/桩 agent 无 LLM → 跳过
                return content
            # 仅对通过基本有效性的草稿反思——污染/过短草稿交由既有重试/占位符逻辑处理。
            if _looks_contaminated(content):
                return content
            instruction = self._critique_section_draft(section, content, previous_sections)
            if not instruction:                           # PASS → 采纳原草稿
                return content
            revised = self._revise_section_draft(section, outline, content, instruction)
            if revised and not _looks_contaminated(revised):
                logger.info(
                    f"章节 {section.title}: 反思修订已采纳（{len(content)}→{len(revised)} 字符）"
                    f" ｜指令: {instruction[:80]}"
                )
                return revised
            return content
        except Exception as _re:  # noqa: BLE001 — 反思为旁路增强，失败绝不影响章节产出
            logger.debug(f"章节反思跳过（忽略）: {_re}")
            return content

    def _reflection_spine_probs(self) -> str:
        """把预测骨架情景概率压成一行紧凑串（供反思批判对齐）；无骨架时回退骨架块文本。"""
        fc = getattr(self, "_forecast_spine", None)
        if isinstance(fc, dict) and fc.get("scenarios"):
            rows: List[str] = []
            for s in fc["scenarios"]:
                if not isinstance(s, dict):
                    continue
                try:
                    p = round(float(s.get("probability") or 0.0) * 100)
                except (TypeError, ValueError):
                    continue
                nm = str(s.get("name") or "").strip()[:40]
                if nm:
                    rows.append(f"{nm}: {p}%")
            if rows:
                return "；".join(rows[:8])
        return (getattr(self, "_forecast_spine_block", "") or "")[:800]

    def _critique_section_draft(
        self, section: "ReportSection", content: str, previous_sections: List[str]
    ) -> Optional[str]:
        """RQ-5：一次廉价批判调用。返回 None 表示 PASS，否则返回单条修订指令。"""
        spine_txt = self._reflection_spine_probs()
        signal_txt = (getattr(self, "_signal_pack", "") or "")[:1500]
        prior = "\n\n".join((s or "")[:600] for s in (previous_sections or [])[:6])[:2400]
        floor = MIN_VALID_SECTION_CHARS
        lang = getattr(self, "output_language", None) or "English"
        sys_prompt = (
            "你是一名严格的报告章节质检员。仅依据下方给定材料，判断本章草稿是否同时满足四条标准：\n"
            "1) 概率一致性：正文若提及情景/事件概率，须与【预测骨架概率】一致，不得矛盾；\n"
            "2) 硬数字接地：关键定量声明须带来源标注 [S#]，或与【信号包】中的硬数字一致；\n"
            f"3) 篇幅下限：正文须有不少于 {floor} 字符的实质内容；\n"
            "4) 不复述前序章节：不得大段重复【前序章节摘要】中的内容。\n"
            f"全部满足 ⇒ 只输出 PASS（不要任何多余文字）；否则 ⇒ 只输出一条最关键、可执行、"
            f"具体的修订指令（用{lang}书写，单句，不要解释）。"
        )
        usr_prompt = (
            f"【预测骨架概率】\n{spine_txt or '（无）'}\n\n"
            f"【信号包（硬数字）】\n{signal_txt or '（无）'}\n\n"
            f"【前序章节摘要】\n{prior or '（无）'}\n\n"
            f"【本章标题】{section.title}\n\n"
            f"【本章草稿】\n{content[:6000]}"
        )
        resp = self.llm.chat(
            messages=[{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": usr_prompt}],
            temperature=0.0,
            max_tokens=512,
            tier="fast",
        )
        text = (resp or "").strip()
        if not text:
            return None                                   # 空响应 → 视为 PASS（不冒险改稿）
        head = re.sub(r"[\s`*_\"'。.，,：:]+", "", text)[:8].upper()
        if head.startswith("PASS"):
            return None
        return text[:600]

    def _revise_section_draft(
        self, section: "ReportSection", outline: "ReportOutline",
        content: str, instruction: str
    ) -> str:
        """RQ-5：按修订指令改写章节草稿（至多一次抽取）。只修被指出的问题，保留其余内容。"""
        sys_prompt = self._lang_override() + (
            "你是报告章节修订员。依据给定的『修订指令』改写本章草稿：只修复被指出的问题，"
            "保留其余正确内容、数据与结构，不得引入未在材料中出现的新数字或新引用。"
            "直接输出改写后的完整 Markdown 正文，不要输出解释、前后缀或指令本身。"
        )
        usr_prompt = (
            f"【修订指令】{instruction}\n\n"
            f"【本章标题】{section.title}\n\n"
            f"【待修订草稿】\n{content}"
        )
        revised = self.llm.chat(
            messages=[{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": usr_prompt}],
            temperature=Config.REPORT_AGENT_TEMPERATURE,
            max_tokens=Config.REPORT_AGENT_SECTION_MAX_TOKENS,
        )
        return (revised or "").strip()

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
        system_prompt = self._lang_override() + SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=_section_heading,
            tools_description=self._get_tools_description(),
            tool_usage_hints=self._tool_usage_hints(),  # RPT-7: live 工具集
            **self._section_prompt_kwargs(),  # RQ-1: 篇幅+工具调用范围槽位（随形状伸缩）
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
            # RQ-7: 前序章节切片从固定 [:8000] 升级为按提供方窗口预算化（大窗口携带全量前文，
            # 小窗口守住 8000 floor）。ADAPTIVE_CONTEXT 关闭时 _cap 恒为 8000（行为不变）。
            _cap = self._prior_section_char_budget(8000, len(previous_sections))
            previous_content = "\n\n---\n\n".join(
                (sec[:_cap] + "..." if len(sec) > _cap else sec) for sec in previous_sections
            )
        else:
            previous_content = "（这是第一个章节）"
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content, section_title=section.title,
            **self._section_prompt_kwargs(),  # RQ-1: 篇幅+工具调用范围槽位
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        schemas = self._to_openai_tool_schemas()
        max_iterations = 14  # RQ-1: 10→14，支撑更多工具轮次 + 更长章节的收尾
        max_tool_calls = self.MAX_TOOL_CALLS_PER_SECTION
        tool_calls_count = 0

        for _ in range(max_iterations):
            # REPORT-9: 工具调用未达下限时，本回合的正文会被拒绝（强制继续检索）→ 必为「工具决策回合」，
            # 用较小的 REPORT_AGENT_TOOL_TURN_MAX_TOKENS 抑制长链推理；达到下限后本回合可能直接产出正文，
            # 回到完整 SECTION_MAX_TOKENS 预算以免截断。（原生路径此前用 chat_with_tools 的 4096 默认值。）
            _turn_max_tokens = (
                Config.REPORT_AGENT_SECTION_MAX_TOKENS
                if tool_calls_count >= self.MIN_TOOL_CALLS_PER_SECTION
                else getattr(Config, "REPORT_AGENT_TOOL_TURN_MAX_TOKENS", 8192)
            )
            resp = self.llm.chat_with_tools(
                messages, schemas, temperature=Config.REPORT_AGENT_TEMPERATURE,
                max_tokens=_turn_max_tokens
            )
            calls = resp.get("tool_calls") or []
            content = resp.get("content") or ""

            if calls and tool_calls_count < max_tool_calls:
                # RPT-11: 整批放行会超预算（count=7、上限 8 时 4 连发会执行到 11）。先把批次
                # 裁到剩余预算再构建 assistant 消息——assistant.tool_calls 与 role=tool 回包
                # 必须一一对应，否则下一次 create() 会因缺失 tool_call_id 回包而 400。
                _dropped = calls[max_tool_calls - tool_calls_count:]
                calls = calls[:max_tool_calls - tool_calls_count]
                if _dropped:
                    logger.info(
                        f"章节 {section.title}: 工具批次超出剩余预算，裁掉 {len(_dropped)} 个调用"
                    )
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
                    # RPT-11: 原生路径此前从不写 log_tool_result，agent_log.jsonl 丢失整条证据链。
                    if self.report_logger:
                        try:
                            self.report_logger.log_tool_result(
                                section.title, section_index, c["name"],
                                str(result)[:8000], tool_calls_count
                            )
                        except Exception:
                            pass
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
        ], temperature=Config.REPORT_AGENT_TEMPERATURE, max_tokens=Config.REPORT_AGENT_SECTION_MAX_TOKENS)
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
        system_prompt = self._lang_override() + SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=_section_heading,
            tools_description=self._get_tools_description(),
            tool_usage_hints=self._tool_usage_hints(),  # RPT-7: live 工具集
            **self._section_prompt_kwargs(),  # RQ-1: 篇幅+工具调用范围槽位（随形状伸缩）
        )
        # T4.1: 钉入研究背景档案 + 来源索引，让每章撰写复用真实角色/关系/时间线并按 [S#] 引用。
        system_prompt = self._prepend_research_background(system_prompt)

        # 构建用户prompt - 每个已完成章节各传入最大4000字
        if previous_sections:
            previous_parts = []
            # RQ-7: 每个已完成章节的上下文切片从固定 8000 升级为按提供方窗口预算化——大窗口
            # 模型携带全量前文（避免长研究报告被切碎），小窗口守住 8000 floor（避免重复、保持连贯）。
            # ADAPTIVE_CONTEXT 关闭时 _cap 恒为 8000（行为与历史逐字节一致）。
            _cap = self._prior_section_char_budget(8000, len(previous_sections))
            for sec in previous_sections:
                truncated = sec[:_cap] + "..." if len(sec) > _cap else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "（这是第一个章节）"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
            **self._section_prompt_kwargs(),  # RQ-1: 篇幅+工具调用范围槽位
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # ReACT循环
        tool_calls_count = 0
        max_iterations = 14  # RQ-1: 10→14，最大迭代轮数（更高以支撑更深入的检索与更长的章节）
        min_tool_calls = self.MIN_TOOL_CALLS_PER_SECTION  # T4.4: 从 Config 读取（默认 4）
        conflict_retries = 0  # 工具调用与Final Answer同时出现的连续冲突次数
        contamination_retries = 0  # 输出被污染（系统提示泄漏/工具调用残留）的连续重试次数
        MAX_CONTAMINATION_RETRIES = 2  # 污染输出最多纠正重试次数
        used_tools = set()  # 记录已调用过的工具名
        # REPORT-8: interview_agents 仍可用（保留在 self.tools / 提示词中），但不再进入
        # 「未使用工具」推荐集——采访依赖 OASIS 在线、延迟高且易超时，不应被反复 nudge 去尝试。
        all_tools = {"insight_forge", "panorama_search", "quick_search",
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
            # REPORT-9: 工具调用未达下限的回合必为「工具决策回合」（此时 Final Answer 会被拒绝、
            # 强制继续检索），用较小的 REPORT_AGENT_TOOL_TURN_MAX_TOKENS 抑制长链推理浪费；一旦达
            # 到下限，本回合可能直接产出最终正文，回到完整 SECTION_MAX_TOKENS 预算以免截断正文。
            # REPORT-10: 温度统一读 Config.REPORT_AGENT_TEMPERATURE（默认 0.5，行为不变、可运维调）。
            _turn_max_tokens = (
                Config.REPORT_AGENT_SECTION_MAX_TOKENS
                if tool_calls_count >= min_tool_calls
                else getattr(Config, "REPORT_AGENT_TOOL_TURN_MAX_TOKENS", 8192)
            )
            response = self.llm.chat(
                messages=messages,
                temperature=Config.REPORT_AGENT_TEMPERATURE,
                max_tokens=_turn_max_tokens
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

                # RPT-7: XML 解析出的工具名在此校验——未知名（如已被移除的 interview_agents）
                # 给纠正性 Observation，且**不**计入工具调用预算（此前垃圾调用照样 +1）。
                # self.tools 为空（仅测试替身场景）时跳过校验，保持旧直通行为。
                if self.tools and call["name"] not in self._valid_tool_names():
                    logger.warning(f"章节 {section.title}: 模型调用了未知工具 '{call['name']}'，已纠正（不计预算）")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"【工具错误】'{call['name']}' 不是可用工具。请从以下工具中选择重新调用："
                            f"{', '.join(sorted(self.tools.keys()))}"
                        ),
                    })
                    continue

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

        # REPORT-9: 这是强制收尾的「最终答案回合」，保留完整 SECTION_MAX_TOKENS 预算以免截断正文。
        # REPORT-10: 温度读 Config.REPORT_AGENT_TEMPERATURE（默认 0.5，行为不变）。
        response = self.llm.chat(
            messages=messages,
            temperature=Config.REPORT_AGENT_TEMPERATURE,
            max_tokens=Config.REPORT_AGENT_SECTION_MAX_TOKENS
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

            # R2-DETAIL-2 / NEXTSTEPS P0-1: 先于大纲推导「预测骨架」，使章节围绕可证伪的预测组织，
            # 而非反向从成稿叙事抽取。顺序：构建确定性信号包（若开）→ 推导骨架（含早落 forecast.json）
            # → 把骨架块 + 强制结构（框架/逐情景/校准）传入 plan_outline。报告文件夹已于上文创建，
            # 骨架早落安全。任一旗标关闭或推导失败时 self._forecast_spine_block 为空串，plan_outline
            # 退回历史行为（提示词逐字节一致），章节阶段亦不受影响（degrade-safe）。
            if getattr(Config, "REPORT_SIGNAL_PACK", False) and not self._signal_pack:
                try:
                    self._signal_pack = self._build_signal_pack()
                    if self._signal_pack:
                        logger.info(f"已注入模拟量化信号包（{len(self._signal_pack)} 字）到各章节提示词")
                except Exception as _sp_err:  # noqa: BLE001 — 信号包为可选增强，失败不影响主流程
                    logger.warning(f"构建模拟量化信号包失败（忽略）: {_sp_err}")
                    self._signal_pack = ""

            # 预测市场信号包（Polymarket 公开 Gamma API）：与模拟信号包并列注入每章
            # 提示词与预测骨架（下方 _derive_and_pin_forecast_spine 复用）。无 key/无数据/
            # 关闭旗标时为空串 → 注入自动跳过（degrade-safe，提示词与历史逐字节一致）。
            if (getattr(Config, "PREDICTION_MARKETS_ENABLED", True)
                    and not getattr(self, "_market_pack", "")):
                try:
                    self._market_pack = self._build_market_pack()
                    if self._market_pack:
                        logger.info(
                            f"已注入预测市场信号包（{len(getattr(self, '_prediction_markets', []))} 个市场）"
                            "到各章节提示词")
                except Exception as _mp_err:  # noqa: BLE001 — 市场信号为可选增强
                    logger.warning(f"构建预测市场信号包失败（忽略）: {_mp_err}")
                    self._market_pack = ""

            if (getattr(Config, "REPORT_STRUCTURED_FORECAST", True)
                    and getattr(Config, "REPORT_FORECAST_SPINE_FIRST", True)):
                self._derive_and_pin_forecast_spine(report_id)

            _spine_ready = bool(self._forecast_spine and self._forecast_spine.get("scenarios"))
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg:
                    progress_callback(stage, prog // 5, msg) if progress_callback else None,
                forecast_spine_block=self._forecast_spine_block,
                require_forecast_structure=_spine_ready,
            )
            report.outline = outline
            # RPT-5: 供引用溯源审计豁免系统注入的摘要 blockquote（"> {outline.summary}"）。
            self._outline_summary = outline.summary or ""

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

            # R2-DETAIL-2: 信号包与预测骨架已在大纲规划前构建/推导（见上），此处不再重复；
            # self._signal_pack / self._forecast_spine_block 已就绪并将注入每章提示词。

            total_sections = len(outline.sections)
            generated_sections = []  # 保存内容用于上下文
            failed_section_titles = []  # 记录生成失败（写入占位符）的章节，用于状态汇报
            # RPT-2(b): 连续失败计数。大纲已降级（LLM 故障前哨）+ 前两章接连落占位符 ⇒
            # 判定为系统性 LLM 中断，快速中止（走既有失败路径标记 FAILED，可重试），
            # 而非对着死掉的提供方烧完余下章节。并发路径的 _gen_body 内部吞异常返回
            # 占位符，故计数以「占位符结果」为准（两条路径统一覆盖）。
            _consecutive_failures = 0
            _abort_on_outage = bool(getattr(Config, "REPORT_ABORT_ON_LLM_OUTAGE", True))

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
                        # RPT-2: 经退避重试包装，瞬时故障不再直接落占位符。
                        _prev_ctx = [_section_brief] if (_context_mode == "brief" and _section_brief) else generated_sections
                        section_content = self._generate_section_with_retry(
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
                    _consecutive_failures += 1
                    logger.error(f"章节生成失败（已写入占位符）: {section.title}")
                    # RPT-2(b): 大纲降级 + 前两章接连失败 ⇒ 系统性 LLM 中断，快速中止。
                    # 抛出 → 既有失败路径统一落盘 FAILED + update_progress('failed')（可重试），
                    # 上游 S1 健康门不必再事后猜测「假完成」。
                    if (_abort_on_outage and self._outline_degraded
                            and section_num <= 2 and _consecutive_failures >= 2):
                        raise RuntimeError(
                            "疑似 LLM 服务中断：大纲规划已降级且前两个章节接连生成失败，"
                            "提前中止本报告（修复提供方后可重试）"
                        )
                else:
                    _consecutive_failures = 0
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
            
            # RPT-2(c): 全部章节均为失败占位符 ⇒ 报告没有任何可用内容，绝不能标记 completed
            # （历史上 91f5 就是 5/5 占位符仍 status=completed，靠编排器健康门事后击杀）。
            # 抛出 → 既有失败路径统一持久化 FAILED + update_progress('failed')，且先于
            # _finalize_structured_forecast（不给空报告生成 forecast.json 的机会）。
            if (_abort_on_outage and total_sections > 0
                    and len(failed_section_titles) >= total_sections):
                raise RuntimeError(
                    f"全部 {total_sections} 个章节生成失败（疑似 LLM 服务中断），"
                    "报告标记为失败（可重试），不写入全占位符的『完成』报告"
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
                    self._finalize_structured_forecast(report_id, report.markdown_content,
                                                       report=report)
                except Exception as _fe:  # noqa: BLE001
                    logger.warning(f"结构化预测最终化失败（忽略，不影响主报告）: {_fe}")
                # QUALITY-OPT B1: prepend the deterministic Part-1 binary-forecast table so the
                # brief's headline deliverable always appears and always matches forecast.json.
                if getattr(Config, "FORECAST_EMIT_BINARY", True):
                    try:
                        self._prepend_binary_forecasts_section(report_id, report)
                    except Exception as _bs_err:  # noqa: BLE001
                        logger.warning(f"前置二元预测章节失败（忽略）: {_bs_err}")
                # VIZ-1：确定性可视化注入——由已落盘工件生成图表（charts/*.mmd + *.png +
                # viz_manifest.json），Mermaid 按章节关键词就地插入、未匹配图 + PNG 归入
                # 「Visual Annex」。放在 Part-1 前置之后、三部骨架之前，使图属于详细章节区
                # （随后被 Part 3 附录标题包住）。任何失败一行告警并跳过（图仍落盘，degrade-safe）。
                if getattr(Config, "REPORT_VISUALIZATIONS", True):
                    try:
                        self._inject_visualizations(report_id, report)
                    except Exception as _viz_err:  # noqa: BLE001
                        logger.warning(f"报告可视化注入失败（忽略，成稿不含图区）: {_viz_err}")
                # B2: 三部结构骨架——Part 1（二元预测表）之后插入 Part 2「框架与综合」
                # （一次 LLM 紧凑综合），再以 Part 3「附录：详细分析」标题包住既有章节。
                # 仅当 Part 1 已插入时生效；综合失败一行告警并跳过（绝不写占位符）。
                if getattr(Config, "REPORT_THREE_PART_SKELETON", True):
                    try:
                        self._apply_three_part_skeleton(report_id, report)
                    except Exception as _tp_err:  # noqa: BLE001
                        logger.warning(f"三部结构骨架套用失败（忽略，保留原结构）: {_tp_err}")
                # NEXTSTEPS P2-2: 追加确定性「如何验证本预测」章节（判定标准 + 观察指标）。
                if (getattr(Config, "REPORT_RESOLUTION_SECTION", True)
                        and self._forecast_spine and self._forecast_spine.get("scenarios")):
                    try:
                        self._append_resolution_section(report_id, report)
                    except Exception as _rs_err:  # noqa: BLE001
                        logger.warning(f"追加判定标准章节失败（忽略）: {_rs_err}")
                # RQ-2：语言纯度扫描——放在最后，让 Part-1 / 三部 / 判定章节等系统注入内容
                # 也一并纳入纯度检查。非 CJK 目标里的 CJK 片段（或反之）批量内联翻译。
                if getattr(Config, "REPORT_LANGUAGE_PURITY", True):
                    try:
                        self._apply_language_purity(report_id, report)
                    except Exception as _lp_err:  # noqa: BLE001
                        logger.warning(f"语言纯度扫描失败（忽略，保留原文）: {_lp_err}")

            # BILINGUAL：在所有最终化/可视化/纯度处理之后（成稿已定型），自动生成另一语种版本
            # （英⇄中）。逐 H2 章节并发翻译，落 full_report.{en|zh}.md 并把 translations 条目写入
            # report（下方 save_report 持久化进 meta.json）。放在 REPORT_STRUCTURED_FORECAST 块之外，
            # 使其无论是否开启结构化预测都能运行。完全 degrade-safe：绝不改主报告，失败仅告警。
            if getattr(Config, "REPORT_BILINGUAL", True):
                try:
                    self._generate_bilingual_report(report_id, report)
                except Exception as _bl_err:  # noqa: BLE001 — 双语为旁路增强，失败不影响主报告
                    logger.warning(f"双语报告生成失败（忽略，不影响主报告）: {_bl_err}")

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
            # XRUN-7: 终态 progress.json 如实记账——占位符章节单列 failed_sections（并从
            # completed_sections 剔除），附 forecast.json 是否产出；status 语义保持不变。
            _forecast_ok = os.path.exists(
                os.path.join(ReportManager._get_report_folder(report_id), "forecast.json")
            )
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "报告生成完成",
                completed_sections=completed_section_titles,
                failed_sections=failed_section_titles,
                forecast_ok=_forecast_ok
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

    # BILINGUAL：合法目标语种代码（同时用于路径构造与 API 校验，单一真源）。
    _TRANSLATION_LANGS = ("en", "zh")

    @classmethod
    def _get_report_translation_path(cls, report_id: str, lang: str) -> str:
        """获取双语版本 Markdown 文件路径 reports/{id}/full_report.<lang>.md（lang ∈ {en, zh}）。"""
        return os.path.join(cls._get_report_folder(report_id), f"full_report.{lang}.md")

    # ── PDF-1: full_report.md → full_report.pdf（pandoc+xelatex，回退 PyMuPDF；按 mtime 缓存）──

    # 已知的 pandoc / xelatex 绝对路径（Homebrew / MacTeX）——运行时校验存在，缺失回退 PATH
    # 查找，再缺失则整条 pandoc 路径不可用（触发 PyMuPDF 回退）。单一真源，避免散落。
    _PANDOC_BIN = "/opt/homebrew/bin/pandoc"
    _XELATEX_BIN = "/Library/TeX/texbin/xelatex"

    @classmethod
    def _get_report_pdf_path(cls, report_id: str, lang: Optional[str] = None) -> str:
        """PDF 导出产物路径。lang ∈ {en, zh} → full_report.<lang>.pdf（双语版 PDF）；
        否则 → full_report.pdf（主报告，行为与历史一致）。"""
        name = f"full_report.{lang}.pdf" if lang in cls._TRANSLATION_LANGS else "full_report.pdf"
        return os.path.join(cls._get_report_folder(report_id), name)

    @staticmethod
    def _is_pdf_file(path: str) -> bool:
        """校验文件确为 PDF（首字节 %PDF-）。pandoc 若因输出格式误判吐出 HTML/文本，此门拦截
        并触发回退，避免把非 PDF 当成功。读失败 → False。"""
        try:
            with open(path, "rb") as f:
                return f.read(5) == b"%PDF-"
        except OSError:
            return False

    @staticmethod
    def _safe_unlink(*paths: str) -> None:
        """静默删除临时文件（不存在/权限错误一律忽略）。"""
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    @staticmethod
    def _rewrite_chart_paths_for_pdf(md: str, folder: str) -> str:
        """PDF-1 预处理：把成稿里相对图表引用（(./)?charts/<file>）重写为绝对文件路径，供 PDF
        构建器（pandoc / PyMuPDF）在任意工作目录都能定位图片。纯字符串变换（可测，无子进程）。"""
        abs_charts = os.path.join(os.path.abspath(folder), "charts")

        def _sub(m: "re.Match") -> str:
            rel = m.group("rel")                       # 形如 charts/foo.png 或 ./charts/foo.png
            fname = rel.split("charts/", 1)[1]
            return "(" + os.path.join(abs_charts, fname) + ")"

        # 仅匹配 markdown 图片/链接目标括号里的相对 charts 路径；绝对路径（/…/charts）不受影响。
        return re.sub(r"\((?P<rel>\.{0,2}/?charts/[^)\s]+)\)", _sub, md)

    @staticmethod
    def _prerender_mermaid_for_pdf(md: str, folder: str) -> str:
        """PDF-1 预处理：PATH 有 mmdc 时把 ```mermaid``` 块预渲染成 PNG 并替换为绝对图片引用；
        无 mmdc 则原样保留围栏（pandoc 当作代码块排版）。degrade-safe：单块渲染失败即保留围栏。"""
        import shutil
        import subprocess
        import hashlib

        mmdc = shutil.which("mmdc")
        if not mmdc:
            return md
        charts_dir = os.path.join(os.path.abspath(folder), "charts")
        try:
            os.makedirs(charts_dir, exist_ok=True)
        except OSError:
            return md
        fence_re = re.compile(r"```mermaid[ \t]*\n(.*?)\n```", re.DOTALL)

        def _sub(m: "re.Match") -> str:
            code = m.group(1)
            h = hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]
            src = os.path.join(charts_dir, f"mermaid_{h}.mmd")
            out = os.path.join(charts_dir, f"mermaid_{h}.png")
            try:
                if not os.path.exists(out):
                    with open(src, "w", encoding="utf-8") as f:
                        f.write(code)
                    subprocess.run([mmdc, "-i", src, "-o", out],
                                   capture_output=True, timeout=60, check=True)
                if os.path.exists(out) and os.path.getsize(out) > 0:
                    return f"![]({out})"
            except Exception:  # noqa: BLE001 — 渲染失败保留原围栏
                return m.group(0)
            return m.group(0)

        return fence_re.sub(_sub, md)

    @classmethod
    def _resolve_pandoc(cls):
        """返回 (pandoc_path, xelatex_path|None)；pandoc 不可用 → None。"""
        import shutil
        pandoc = cls._PANDOC_BIN if os.path.exists(cls._PANDOC_BIN) else shutil.which("pandoc")
        if not pandoc:
            return None
        xelatex = cls._XELATEX_BIN if os.path.exists(cls._XELATEX_BIN) else shutil.which("xelatex")
        return pandoc, xelatex

    @classmethod
    def _export_pdf_pandoc(cls, report_id: str, md: str, folder: str, pdf_path: str) -> bool:
        """pandoc + xelatex 构建 PDF（CJKmainfont=PingFang SC、geometry margin 2.5cm、--toc）。
        成功（产出非空 PDF）→ True；pandoc 不可用/返回非零/无产出/超时 → False（触发回退）。"""
        import subprocess
        resolved = cls._resolve_pandoc()
        if not resolved:
            return False
        pandoc, xelatex = resolved
        # 预处理后的成稿写临时 _pdf_source.md，pandoc 以它为输入（相对资源已绝对化，cwd=folder）。
        src_md = os.path.join(folder, "_pdf_source.md")
        # 临时产物必须以 .pdf 结尾——pandoc 由输出扩展名推断格式，用 .tmp 会退化为 HTML 而非 PDF。
        tmp_pdf = os.path.join(folder, "_full_report.building.pdf")
        try:
            with open(src_md, "w", encoding="utf-8") as f:
                f.write(md)
        except OSError as e:
            logger.warning(f"写 PDF 源 md 失败: {e}")
            return False
        cmd = [
            pandoc, src_md,
            f"--pdf-engine={xelatex or 'xelatex'}",
            "-V", "CJKmainfont=PingFang SC",
            "-V", "geometry:margin=2.5cm",
            "--toc",
            "-o", tmp_pdf,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, cwd=folder,
                timeout=int(getattr(Config, "REPORT_PDF_TIMEOUT", 180) or 180),
            )
        except Exception as e:  # noqa: BLE001 — 超时/OS 错误 → 回退
            logger.warning(f"pandoc 执行异常，回退 PyMuPDF: {e}")
            cls._safe_unlink(src_md, tmp_pdf)
            return False
        rc = getattr(proc, "returncode", 1)
        if rc != 0 or not os.path.exists(tmp_pdf) or os.path.getsize(tmp_pdf) == 0 \
                or not cls._is_pdf_file(tmp_pdf):
            _stderr = getattr(proc, "stderr", b"") or b""
            _tail = _stderr[-500:].decode("utf-8", "ignore") if isinstance(_stderr, (bytes, bytearray)) else str(_stderr)[-500:]
            logger.warning(f"pandoc 未产出有效 PDF（rc={rc}），回退 PyMuPDF：{_tail}")
            cls._safe_unlink(src_md, tmp_pdf)
            return False
        try:
            os.replace(tmp_pdf, pdf_path)
        except OSError as e:
            logger.warning(f"PDF 原子替换失败: {e}")
            cls._safe_unlink(src_md, tmp_pdf)
            return False
        cls._safe_unlink(src_md)
        logger.info(f"pandoc 导出 PDF 成功: {report_id}")
        return True

    @classmethod
    def _export_pdf_pymupdf(cls, md: str, folder: str, pdf_path: str) -> bool:
        """回退：markdown→基础 HTML→PyMuPDF Story 排版为 PDF（A4、2.5cm 边距、自动分页）。
        pymupdf 缺失/渲染失败 → False。图片经绝对路径 + folder 归档尽力解析（缺图仍出文本）。"""
        try:
            import fitz  # PyMuPDF
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PyMuPDF 不可用，无法回退导出 PDF: {e}")
            return False
        tmp_pdf = pdf_path + ".tmp"
        try:
            html = cls._markdown_to_basic_html(md)
            page_rect = fitz.paper_rect("a4")
            margin = 71  # ≈2.5cm（1cm≈28.35pt）
            where = fitz.Rect(page_rect.x0 + margin, page_rect.y0 + margin,
                              page_rect.x1 - margin, page_rect.y1 - margin)
            try:
                arch = fitz.Archive(os.path.abspath(folder))
                story = fitz.Story(html=html, archive=arch)
            except Exception:  # noqa: BLE001 — 归档失败仍可排版文本
                story = fitz.Story(html=html)
            writer = fitz.DocumentWriter(tmp_pdf)
            more = 1
            guard = 0
            while more and guard < 2000:  # guard：防病态 HTML 造成无限分页
                dev = writer.begin_page(page_rect)
                more, _ = story.place(where)
                story.draw(dev)
                writer.end_page()
                guard += 1
            writer.close()
            if not os.path.exists(tmp_pdf) or os.path.getsize(tmp_pdf) == 0:
                cls._safe_unlink(tmp_pdf)
                return False
            os.replace(tmp_pdf, pdf_path)
            logger.info("PyMuPDF 回退导出 PDF 成功")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PyMuPDF 回退导出失败: {e}")
            cls._safe_unlink(tmp_pdf)
            return False

    @staticmethod
    def _markdown_to_basic_html(md: str) -> str:
        """极简 markdown→HTML（仅回退路径用；无外部依赖）。覆盖 #~###### 标题、无序/有序列表、
        ```围栏代码块、图片 ![alt](src)、**粗体**/*斜体*/`行内码`、段落。未识别行按段落处理，
        HTML 特殊字符转义（图片 src 不转义）。目标是「文本可读 + 尽力显示图」，非像素级还原。"""
        import html as _html

        def _inline(text: str) -> str:
            # 先抽出图片，避免其 URL 被转义/干扰其它行内规则。
            imgs: List[str] = []

            def _img(m: "re.Match") -> str:
                alt = _html.escape(m.group(1))
                src = m.group(2).strip()
                imgs.append(f'<img src="{src}" alt="{alt}" style="max-width:100%;"/>')
                return f"\x00{len(imgs) - 1}\x00"

            text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _img, text)
            text = _html.escape(text)
            text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
            text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
            text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
            # 还原图片占位符。
            text = re.sub(r"\x00(\d+)\x00", lambda m: imgs[int(m.group(1))], text)
            return text

        lines = (md or "").split("\n")
        out: List[str] = []
        in_code = False
        code_buf: List[str] = []
        list_type: Optional[str] = None  # 'ul' | 'ol' | None

        def _close_list() -> None:
            nonlocal list_type
            if list_type:
                out.append(f"</{list_type}>")
                list_type = None

        for ln in lines:
            if ln.strip().startswith("```"):
                if in_code:
                    out.append("<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>")
                    code_buf = []
                    in_code = False
                else:
                    _close_list()
                    in_code = True
                continue
            if in_code:
                code_buf.append(ln)
                continue
            s = ln.strip()
            if not s:
                _close_list()
                continue
            hm = re.match(r"^(#{1,6})\s+(.*)$", s)
            if hm:
                _close_list()
                lvl = len(hm.group(1))
                out.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
                continue
            om = re.match(r"^\d+[.)]\s+(.*)$", s)
            um = re.match(r"^[-*+]\s+(.*)$", s)
            if om:
                if list_type != "ol":
                    _close_list()
                    out.append("<ol>")
                    list_type = "ol"
                out.append(f"<li>{_inline(om.group(1))}</li>")
                continue
            if um:
                if list_type != "ul":
                    _close_list()
                    out.append("<ul>")
                    list_type = "ul"
                out.append(f"<li>{_inline(um.group(1))}</li>")
                continue
            _close_list()
            out.append(f"<p>{_inline(s)}</p>")
        if in_code:  # 未闭合围栏兜底
            out.append("<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>")
        _close_list()
        body = "\n".join(out)
        return ("<html><head><meta charset='utf-8'><style>"
                "body{font-family:sans-serif;font-size:11pt;line-height:1.4;}"
                "h1{font-size:20pt;}h2{font-size:16pt;}h3{font-size:13pt;}"
                "pre{background:#f4f4f4;padding:6px;font-size:9pt;white-space:pre-wrap;}"
                "code{font-family:monospace;}img{margin:6px 0;}"
                "</style></head><body>" + body + "</body></html>")

    @classmethod
    def export_pdf(cls, report_id: str, force: bool = False,
                   lang: Optional[str] = None) -> Optional[str]:
        """PDF-1: 惰性把成稿导出为 PDF 并缓存（按源 md 的 mtime 失效）。

        流水：① 读成稿 → 相对图表路径绝对化 + （PATH 有 mmdc 时）预渲染 Mermaid 为 PNG；
        ② pandoc+xelatex（CJKmainfont=PingFang SC、geometry margin 2.5cm、--toc）；
        ③ 失败回退 markdown→HTML→PyMuPDF Story。关闭 REPORT_PDF_EXPORT / 无成稿 → None
        （degrade-safe）。返回 PDF 绝对路径或 None。

        BILINGUAL：lang ∈ {en, zh} 时以双语版 full_report.<lang>.md 为源、产出
        full_report.<lang>.pdf（复用同一套 export 机制）；非法/缺省 lang → 主报告
        full_report.md → full_report.pdf（默认，行为与历史逐字节一致）。

        缓存：PDF 存在且 mtime ≥ 源 md mtime → 命中直接返回（force=True 强制重建）。"""
        if not getattr(Config, "REPORT_PDF_EXPORT", True):
            return None
        # 校验 lang：仅 {en, zh} 视为双语请求，其余（含 None/非法值）回退主报告。
        lang = lang if lang in cls._TRANSLATION_LANGS else None
        md_path = (cls._get_report_translation_path(report_id, lang)
                   if lang else cls._get_report_markdown_path(report_id))
        if not os.path.exists(md_path):
            return None
        folder = cls._get_report_folder(report_id)
        pdf_path = cls._get_report_pdf_path(report_id, lang)
        # mtime 缓存：PDF 不早于成稿即命中。full_report.md 更新后其 mtime 变新 → 自动失效重建。
        try:
            if (not force and os.path.exists(pdf_path)
                    and os.path.getmtime(pdf_path) >= os.path.getmtime(md_path)):
                return pdf_path
        except OSError:
            pass
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                md = f.read()
        except OSError as e:
            logger.warning(f"读取 full_report.md 失败，无法导出 PDF: {e}")
            return None
        # 预处理：绝对化图表路径 + 预渲染 Mermaid（无 mmdc 则保留围栏）。失败回退用原始成稿。
        try:
            proc_md = cls._rewrite_chart_paths_for_pdf(md, folder)
            proc_md = cls._prerender_mermaid_for_pdf(proc_md, folder)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PDF 预处理失败，回退用原始成稿: {e}")
            proc_md = md
        # 主路径：pandoc + xelatex；失败回退 PyMuPDF Story。
        if cls._export_pdf_pandoc(report_id, proc_md, folder, pdf_path):
            return pdf_path
        if cls._export_pdf_pymupdf(proc_md, folder, pdf_path):
            return pdf_path
        logger.warning(f"PDF 导出失败（pandoc 与 PyMuPDF 回退均未产出）: {report_id}")
        return None

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
        2. RQ-1：保留 ### 三级小标题（章节内部子小节结构），把 #/##（H1/H2 层级，与报告主标题/
           章节标题冲突）与 #### 及更深层级一律降级为粗体文本

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

                # RQ-1：白名单 ### 三级小标题——章节内部子小节结构，原样保留（H3 纪律）。
                if level == 3:
                    cleaned_lines.append(f"### {title_text}")
                    continue
                # 其余层级（#/##/#### 及更深）降级为粗体：H1/H2 与报告主标题/章节标题层级冲突，
                # 深层级破坏单层子小节纪律。章节主标题由系统添加，正文不应出现这些层级。
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
        completed_sections: List[str] = None,
        failed_sections: List[str] = None,
        forecast_ok: bool = None
    ) -> None:
        """
        更新报告生成进度

        前端可以通过读取progress.json获取实时进度

        XRUN-7: failed_sections/forecast_ok 为可选增量字段——占位符章节从
        completed_sections 中剔除并单列，附 placeholder_count 与 health 标记，
        让 progress.json 不再对着 5/5 占位符宣称全部章节完成。二者缺省时
        输出 schema 与历史逐字节一致（degrade-safe）。status 保持原语义不变
        （消费方以 status 判定终态，健康降级经 health 字段表达）。
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
        if failed_sections is not None:
            _failed = list(failed_sections)
            progress_data["completed_sections"] = [
                t for t in (completed_sections or []) if t not in _failed
            ]
            progress_data["failed_sections"] = _failed
            progress_data["placeholder_count"] = len(_failed)
            if _failed:
                progress_data["health"] = "degraded"
        if forecast_ok is not None:
            progress_data["forecast_ok"] = bool(forecast_ok)

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
        2. RQ-1：保留报告主标题(#)、章节标题(##) 与章节内 ### 三级小标题；把 #### 及更深层级
           降级为粗体（H1/H2/H3 三级纪律，H4+ 破坏单层子小节结构）
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
                
                # 标题层级处理（RQ-1）：
                # - # (level=1) 只保留报告主标题
                # - ## (level=2) 保留章节标题
                # - ### (level=3) 保留章节内三级小标题（子小节结构）
                # - #### 及更深 (level>=4) 转换为粗体文本

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
                elif level == 3:
                    # RQ-1：保留章节内 ### 三级小标题（子小节结构）
                    processed_lines.append(f"### {title}")
                    prev_was_heading = True
                else:
                    # #### 及更深层级的标题转换为粗体文本
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
            telemetry=data.get('telemetry'),  # EXECPLAN2 I-5-4: 从 meta.json 还原紧凑遥测，经 API 暴露
            translations=data.get('translations')  # BILINGUAL：从 meta.json 还原双语版本清单
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
