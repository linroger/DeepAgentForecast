"""
Report API路由
提供模拟报告生成、获取、对话等接口
"""

import os
import traceback
import threading
from flask import request, jsonify, send_file, send_from_directory, Response

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.pipeline_orchestrator import load_research_dossier_for_simulation
from ..services.simulation_manager import SimulationManager
from ..models.project import ProjectManager
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.report')


# ============== 报告生成接口 ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    生成模拟分析报告（异步任务）
    
    这是一个耗时操作，接口会立即返回task_id，
    使用 GET /api/report/generate/status 查询进度
    
    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",    // 必填，模拟ID
            "force_regenerate": false        // 可选，强制重新生成
        }
    
    返回：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "报告生成任务已启动"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        force_regenerate = data.get('force_regenerate', False)
        
        # 获取模拟信息
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"模拟不存在: {simulation_id}"
            }), 404
        
        # 检查是否已有报告
        if not force_regenerate:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "message": "报告已存在",
                        "already_generated": True
                    }
                })
        
        # 获取项目信息
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "缺少图谱ID，请确保已构建图谱"
            }), 400
        
        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "缺少模拟需求描述"
            }), 400
        
        # 提前生成 report_id，以便立即返回给前端
        import uuid
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        
        # 创建异步任务
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="report_generate",
            metadata={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "report_id": report_id
            }
        )
        
        # 定义后台任务
        def run_generate():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="初始化Report Agent..."
                )
                
                # 创建Report Agent（T4.1: best-effort 钉入研究档案，找不到则回退冷图路径）
                _dossier = load_research_dossier_for_simulation(simulation_id)
                agent = ReportAgent(
                    graph_id=graph_id,
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    situation_brief=_dossier.get("situation_brief"),
                    actors=_dossier.get("actors"),
                    sources=_dossier.get("sources"),
                    research_report=_dossier.get("research_report"),
                )
                
                # 进度回调
                def progress_callback(stage, progress, message):
                    task_manager.update_task(
                        task_id,
                        progress=progress,
                        message=f"[{stage}] {message}"
                    )
                
                # 生成报告（传入预先生成的 report_id）
                report = agent.generate_report(
                    progress_callback=progress_callback,
                    report_id=report_id
                )
                
                # 保存报告
                ReportManager.save_report(report)

                # EXECPLAN2 F-7-0: force_regenerate 成功后清理同 simulation 的旧报告文件夹，
                # 避免遗留多份导致 get_report_by_simulation 返回过期/不确定的报告。
                if force_regenerate and report.status == ReportStatus.COMPLETED:
                    try:
                        removed = ReportManager.delete_other_reports_for_simulation(
                            simulation_id, keep_report_id=report.report_id
                        )
                        if removed:
                            logger.info(f"已清理 {removed} 份同 simulation 旧报告: {simulation_id}")
                    except Exception as _e:
                        logger.warning(f"清理旧报告失败（忽略）: {_e}")

                if report.status == ReportStatus.COMPLETED:
                    task_manager.complete_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "completed"
                        }
                    )
                else:
                    task_manager.fail_task(task_id, report.error or "报告生成失败")
                
            except Exception as e:
                logger.error(f"报告生成失败: {str(e)}")
                task_manager.fail_task(task_id, str(e))
        
        # 启动后台线程
        thread = threading.Thread(target=run_generate, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": "报告生成任务已启动，请通过 /api/report/generate/status 查询进度",
                "already_generated": False
            }
        })
        
    except Exception as e:
        logger.error(f"启动报告生成任务失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['POST'])
def get_generate_status():
    """
    查询报告生成任务进度
    
    请求（JSON）：
        {
            "task_id": "task_xxxx",         // 可选，generate返回的task_id
            "simulation_id": "sim_xxxx"     // 可选，模拟ID
        }
    
    返回：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # 如果提供了simulation_id，先检查是否已有完成的报告
        if simulation_id:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "progress": 100,
                        "message": "报告已生成",
                        "already_completed": True
                    }
                })
        
        if not task_id:
            return jsonify({
                "success": False,
                "error": "请提供 task_id 或 simulation_id"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                "success": False,
                "error": f"任务不存在: {task_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": task.to_dict()
        })
        
    except Exception as e:
        logger.error(f"查询任务状态失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== 报告获取接口 ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    获取报告详情
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"报告不存在: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict()
        })
        
    except Exception as e:
        logger.error(f"获取报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    根据模拟ID获取报告
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"该模拟暂无报告: {simulation_id}",
                "has_report": False
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })
        
    except Exception as e:
        logger.error(f"获取报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    列出所有报告
    
    Query参数：
        simulation_id: 按模拟ID过滤（可选）
        limit: 返回数量限制（默认50）
    
    返回：
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)
        
        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })
        
    except Exception as e:
        logger.error(f"列出报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    下载报告（Markdown格式）
    
    返回Markdown文件
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"报告不存在: {report_id}"
            }), 404
        
        md_path = ReportManager._get_report_markdown_path(report_id)
        
        if not os.path.exists(md_path):
            # EXECPLAN2 F-7-4: MD文件不存在时直接以内存流返回，避免遗留临时文件导致磁盘/inode泄漏
            return Response(
                report.markdown_content or "",
                mimetype="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{report_id}.md"'
                },
            )
        
        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.md"
        )
        
    except Exception as e:
        logger.error(f"下载报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 双语报告 Markdown 接口（BILINGUAL）==============

@report_bp.route('/<report_id>/full_report.<lang>.md', methods=['GET'])
def get_report_translation_md(report_id: str, lang: str):
    """服务自动生成的双语版本 reports/{id}/full_report.<lang>.md（lang ∈ {en, zh}）。

    镜像 /download 的原文件返回约定：文件存在则以 text/markdown 返回，否则 404。lang 非
    {en, zh} 或该语种版本未生成（REPORT_BILINGUAL 关闭 / 同语种 / 翻译失败）→ 404（degrade-safe）。
    """
    try:
        lang = (lang or '').strip().lower()
        if lang not in ReportManager._TRANSLATION_LANGS:
            return jsonify({
                "success": False,
                "error": f"不支持的语种: {lang}（仅 en / zh）"
            }), 404

        md_path = ReportManager._get_report_translation_path(report_id, lang)
        if not os.path.exists(md_path):
            return jsonify({
                "success": False,
                "error": f"该报告暂无 {lang} 版本: {report_id}"
            }), 404

        return send_file(
            md_path,
            mimetype="text/markdown",
            as_attachment=True,
            download_name=f"{report_id}.{lang}.md",
        )

    except Exception as e:
        logger.error(f"获取双语报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告 PDF 导出接口（PDF-1）==============

@report_bp.route('/<report_id>/pdf', methods=['GET'])
def get_report_pdf(report_id: str):
    """惰性构建并返回报告 PDF（reports/{id}/full_report.pdf）。

    首次访问（或 full_report.md 更新后）触发一次 pandoc+xelatex 构建（相对图表路径绝对化、
    CJK 字体 PingFang SC、--toc；失败回退 markdown→HTML→PyMuPDF），随后按 full_report.md
    的 mtime 缓存复用。REPORT_PDF_EXPORT 关闭 / 报告缺失 / 构建失败 → 404（degrade-safe）。

    BILINGUAL：可选 ?lang=en|zh 从双语版 full_report.<lang>.md 构建 full_report.<lang>.pdf
    （复用同一套 export 机制）；缺省/非法 lang → 主报告 PDF（默认，行为与历史一致）。

    与同蓝图的 /download（Markdown）、/charts（图表资源）共用 /api/report/<id>/... 前缀约定。
    """
    try:
        report = ReportManager.get_report(report_id)
        if not report:
            return jsonify({
                "success": False,
                "error": f"报告不存在: {report_id}"
            }), 404

        # BILINGUAL：?lang= 仅接受 {en, zh}，其余（含缺省）回退主报告。
        lang = (request.args.get('lang') or '').strip().lower()
        lang = lang if lang in ReportManager._TRANSLATION_LANGS else None

        pdf_path = ReportManager.export_pdf(report_id, lang=lang)
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({
                "success": False,
                "error": "PDF 导出不可用（未开启 REPORT_PDF_EXPORT、缺该语种版本或构建失败）"
            }), 404

        download_name = f"{report_id}.{lang}.pdf" if lang else f"{report_id}.pdf"
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="application/pdf",
        )

    except Exception as e:
        logger.error(f"导出报告 PDF 失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告可视化资源接口（VIZ-1）==============

@report_bp.route('/<report_id>/charts/<path:filename>', methods=['GET'])
def get_report_chart(report_id: str, filename: str):
    """服务报告可视化产物 reports/{id}/charts/<file>（PNG 图表 / Mermaid .mmd 代码块）。

    与 full_report.md 同源：文件都落在报告文件夹下，这里以 charts/ 子目录暴露。viz_manifest.json
    里的相对路径（'charts/xxx.png'）对 Web（经此端点）与 PDF（相对 report_dir 的文件系统路径）
    都成立。send_from_directory 负责防目录穿越（filename 越界解析会 404）。
    """
    try:
        charts_dir = os.path.join(
            ReportManager._get_report_folder(report_id), "charts"
        )
        if not os.path.isdir(charts_dir):
            return jsonify({
                "success": False,
                "error": f"报告无可视化产物: {report_id}"
            }), 404
        # send_from_directory 对 filename 做安全解析（拒绝 ../ 逃逸），越界或缺失 → 404
        return send_from_directory(charts_dir, filename)
    except Exception as e:
        # werkzeug 对非法路径抛 NotFound（404）；其余异常统一 404 避免泄漏路径细节
        logger.warning(f"获取报告图表失败: report_id={report_id}, file={filename}, err={e}")
        return jsonify({
            "success": False,
            "error": f"图表不存在: {filename}"
        }), 404


@report_bp.route('/<report_id>/viz-manifest', methods=['GET'])
def get_report_viz_manifest(report_id: str):
    """获取报告可视化清单 reports/{id}/viz_manifest.json（[{path,type,source,caption,placement_hint}]）。

    无清单（未开启可视化 / 无可渲染工件）→ 返回空列表（degrade-safe，前端据此不渲染图区）。
    """
    try:
        import json as _json
        manifest_path = os.path.join(
            ReportManager._get_report_folder(report_id), "viz_manifest.json"
        )
        if not os.path.exists(manifest_path):
            return jsonify({
                "success": True,
                "data": [],
                "count": 0
            })
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = _json.load(f)
        if not isinstance(manifest, list):
            manifest = []
        return jsonify({
            "success": True,
            "data": manifest,
            "count": len(manifest)
        })
    except Exception as e:
        logger.error(f"获取可视化清单失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """删除报告"""
    try:
        success = ReportManager.delete_report(report_id)
        
        if not success:
            return jsonify({
                "success": False,
                "error": f"报告不存在: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "message": f"报告已删除: {report_id}"
        })
        
    except Exception as e:
        logger.error(f"删除报告失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Report Agent对话接口 ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    与Report Agent对话
    
    Report Agent可以在对话中自主调用检索工具来回答问题
    
    请求（JSON）：
        {
            "simulation_id": "sim_xxxx",        // 必填，模拟ID
            "message": "请解释一下舆情走向",    // 必填，用户消息
            "chat_history": [                   // 可选，对话历史
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }
    
    返回：
        {
            "success": true,
            "data": {
                "response": "Agent回复...",
                "tool_calls": [调用的工具列表],
                "sources": [信息来源]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        if not message:
            return jsonify({
                "success": False,
                "error": "请提供 message"
            }), 400
        
        # 获取模拟和项目信息
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"模拟不存在: {simulation_id}"
            }), 404
        
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "缺少图谱ID"
            }), 400
        
        simulation_requirement = project.simulation_requirement or ""
        
        # 创建Agent并进行对话（T4.1: best-effort 钉入研究档案）
        _dossier = load_research_dossier_for_simulation(simulation_id)
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement,
            situation_brief=_dossier.get("situation_brief"),
            actors=_dossier.get("actors"),
            sources=_dossier.get("sources"),
            research_report=_dossier.get("research_report"),
        )

        result = agent.chat(message=message, chat_history=chat_history)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"对话失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告进度与分章节接口 ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    获取报告生成进度（实时）
    
    返回：
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "正在生成章节: 关键发现",
                "current_section": "关键发现",
                "completed_sections": ["执行摘要", "模拟背景"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)
        
        if not progress:
            return jsonify({
                "success": False,
                "error": f"报告不存在或进度信息不可用: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": progress
        })
        
    except Exception as e:
        logger.error(f"获取报告进度失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    获取已生成的章节列表（分章节输出）
    
    前端可以轮询此接口获取已生成的章节内容，无需等待整个报告完成
    
    返回：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## 执行摘要\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)
        
        # 获取报告状态
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })
        
    except Exception as e:
        logger.error(f"获取章节列表失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections-partial', methods=['GET'])
def get_report_sections_partial(report_id: str):
    """ITEM-17：为「仍在生成中」的报告增量返回已完成章节，供前端 2s 轮询渐进式发布。

    返回契约（前端据此逐章渲染，无需等整篇完成）：
        {
            "success": true,
            "sections": [
                {"index": 1, "title": "执行摘要", "status": "completed",
                 "content_md": "## 执行摘要\\n\\n..."},
                {"index": 2, "title": "预测场景", "status": "generating", "content_md": ""}
            ],
            "done": false
        }

    实现完全基于磁盘文件读取（cheap，安全用于 2s 轮询）：
      · sections 取自已落盘的 section_XX.md（每个即一段已完成章节，status='completed'）；
        title 从章节正文首个 markdown 标题行解析，缺失回退 'Section N'。
      · 若 progress.json 记录了 current_section 且不在已完成集合中，追加一个占位条目
        （status='generating'，content_md=''），让前端能显示「正在生成: X」。
      · done=true 当且仅当 full_report.md 已落盘（终稿组装完成）。
    任一底层读取异常都 degrade 成安全空集（不 500，保证轮询稳定）。
    """
    try:
        import re as _re

        def _parse_title(md: str, fallback: str) -> str:
            """从章节正文抽首个 markdown 标题（# ~ ######）作为标题；无标题 → fallback。"""
            for line in (md or "").splitlines():
                m = _re.match(r'^\s*#{1,6}\s+(.+?)\s*$', line)
                if m:
                    return m.group(1).strip()
            return fallback

        # ① 已完成章节（section_XX.md）——每段 status='completed'
        raw_sections = ReportManager.get_generated_sections(report_id)
        sections = []
        completed_titles = set()
        for s in raw_sections:
            idx = s.get("section_index")
            content = s.get("content", "") or ""
            title = _parse_title(content, fallback=f"Section {idx}")
            completed_titles.add(title)
            sections.append({
                "index": idx,
                "title": title,
                "status": "completed",
                "content_md": content,
            })

        # ② 正在生成的章节（来自 progress.json 的 current_section，占位、无正文）
        try:
            progress = ReportManager.get_progress(report_id)
        except Exception:  # noqa: BLE001 - 进度文件缺失/损坏不影响已完成章节返回
            progress = None
        if isinstance(progress, dict):
            current = (progress.get("current_section") or "").strip()
            status = progress.get("status")
            # 仅当报告仍在生成、当前章节有名且尚未落盘时，追加 generating 占位。
            if current and current not in completed_titles and status not in ("completed", "failed"):
                next_index = (max((s["index"] for s in sections if isinstance(s["index"], int)),
                                  default=0) + 1)
                sections.append({
                    "index": next_index,
                    "title": current,
                    "status": "generating",
                    "content_md": "",
                })

        # ③ done：终稿 full_report.md 已落盘即视为完成
        done = os.path.exists(ReportManager._get_report_markdown_path(report_id))

        return jsonify({
            "success": True,
            "sections": sections,
            "done": done,
        })

    except Exception as e:
        logger.error(f"获取分章节增量失败: {str(e)}")
        # degrade-safe：轮询端点即便异常也返回稳定空集，避免前端轮询中断
        return jsonify({
            "success": False,
            "sections": [],
            "done": False,
            "error": str(e),
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    获取单个章节内容
    
    返回：
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## 执行摘要\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)
        
        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": f"章节不存在: section_{section_index:02d}.md"
            }), 404
        
        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })
        
    except Exception as e:
        logger.error(f"获取章节内容失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 报告状态检查接口 ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    检查模拟是否有报告，以及报告状态
    
    用于前端判断是否解锁Interview功能
    
    返回：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None
        
        # 只有报告完成后才解锁interview
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })
        
    except Exception as e:
        logger.error(f"检查报告状态失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent 日志接口 ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    获取 Report Agent 的详细执行日志
    
    实时获取报告生成过程中的每一步动作，包括：
    - 报告开始、规划开始/完成
    - 每个章节的开始、工具调用、LLM响应、完成
    - 报告完成或失败
    
    Query参数：
        from_line: 从第几行开始读取（可选，默认0，用于增量获取）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "执行摘要",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"获取Agent日志失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    获取完整的 Agent 日志（一次性获取全部）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"获取Agent日志失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 控制台日志接口 ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    获取 Report Agent 的控制台输出日志
    
    实时获取报告生成过程中的控制台输出（INFO、WARNING等），
    这与 agent-log 接口返回的结构化 JSON 日志不同，
    是纯文本格式的控制台风格日志。
    
    Query参数：
        from_line: 从第几行开始读取（可选，默认0，用于增量获取）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: 搜索完成: 找到 15 条相关事实",
                    "[19:46:14] INFO: 图谱搜索: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_console_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"获取控制台日志失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    获取完整的控制台日志（一次性获取全部）
    
    返回：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"获取控制台日志失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 工具调用接口（供调试使用）==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    图谱搜索工具接口（供调试使用）
    
    请求（JSON）：
        {
            "graph_id": "mirofish_xxxx",
            "query": "搜索查询",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)
        
        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": "请提供 graph_id 和 query"
            }), 400
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"图谱搜索失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    图谱统计工具接口（供调试使用）
    
    请求（JSON）：
        {
            "graph_id": "mirofish_xxxx"
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "请提供 graph_id"
            }), 400
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.get_graph_statistics(graph_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"获取图谱统计失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
