"""统一研究→预测管线 API (Step 0)

一个 prompt 进：DeerFlow 深度研究 → MiroFish 知识图谱 → OASIS 模拟 → 预测报告。

路由（挂载于 /api/research）：
    POST   /run                      启动管线，立即返回 {pipeline_id, task_id}
    POST   /<pipeline_id>/cancel     取消在飞管线（杀研究子进程 / 停 OASIS 模拟）
    POST   /<pipeline_id>/resume     从失败/取消的管线继续（复用已完成产物）
    GET    /status/<pipeline_id>     聚合的五阶段进度
    GET    /list                     最近管线列表
    GET    /<pipeline_id>/dossier    研究报告 markdown + actors/sources
    GET    /<pipeline_id>/progress   研究子进程进度日志（tail）
"""

import os
import traceback

from flask import jsonify, request

from . import research_bp
from ..config import Config
from ..services.pipeline_orchestrator import PipelineManager, PipelineOrchestrator, preflight_pipeline
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.research')

_VALID_DEPTH = {"quick", "standard", "deep"}
_VALID_MODE = {"full", "research_only"}


@research_bp.route('/run', methods=['POST'])
def run_pipeline():
    """启动统一管线。

    请求 (application/json):
        prompt: str            预测/研究问题（必填）
        mode: str              full | research_only（默认 full）
        project_name: str      可选
        depth: str             quick | standard | deep（默认 standard）
        max_rounds: int        OASIS 最大轮数（可选，截断模拟）
    """
    try:
        data = request.get_json(silent=True) or {}
        prompt = (data.get('prompt') or '').strip()
        if not prompt:
            return jsonify({"success": False, "error": "缺少 prompt"}), 400

        mode = (data.get('mode') or 'full').strip().lower()
        if mode not in _VALID_MODE:
            return jsonify({"success": False, "error": f"mode 必须是 {_VALID_MODE} 之一"}), 400

        depth = (data.get('depth') or Config.DEERFLOW_RESEARCH_DEPTH).strip().lower()
        if depth not in _VALID_DEPTH:
            return jsonify({"success": False, "error": f"depth 必须是 {_VALID_DEPTH} 之一"}), 400

        max_rounds = data.get('max_rounds')
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "max_rounds 必须是整数"}), 400

        # 起飞前体检：把"研究跑完 40 分钟后才发现 Zep Key 是占位符"这类失败提前到现在
        preflight_errors = preflight_pipeline(mode=mode)
        if preflight_errors:
            return jsonify({
                "success": False,
                "error": "启动前检查未通过：\n" + "\n".join(f"• {e}" for e in preflight_errors),
                "preflight_errors": preflight_errors,
            }), 400

        state = PipelineOrchestrator.start(
            prompt=prompt,
            mode=mode,
            project_name=data.get('project_name'),
            depth=depth,
            max_rounds=max_rounds,
        )
        return jsonify({
            "success": True,
            "data": {
                "pipeline_id": state.pipeline_id,
                "task_id": state.task_id,
                "mode": state.mode,
                "status": state.status,
            },
        })
    except Exception as e:
        logger.error(f"启动管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@research_bp.route('/<pipeline_id>/cancel', methods=['POST'])
def cancel_pipeline(pipeline_id: str):
    """取消在飞管线。研究阶段杀子进程组；模拟阶段停 OASIS；其余阶段在下个取消点退出。"""
    try:
        result = PipelineOrchestrator.cancel(pipeline_id)
        if result["status"] == "not_found":
            return jsonify({"success": False, "error": "管线不存在"}), 404
        if result["status"] == "not_running":
            return jsonify({"success": False, "error": "管线已结束，无法取消"}), 409
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"取消管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@research_bp.route('/<pipeline_id>/resume', methods=['POST'])
def resume_pipeline(pipeline_id: str):
    """恢复失败/取消的管线，尽量复用已有研究、图谱、模拟等阶段产物。"""
    try:
        existing = PipelineManager.load(pipeline_id)
        if existing is None:
            return jsonify({"success": False, "error": "管线不存在"}), 404
        preflight_errors = preflight_pipeline(mode=existing.get("mode") or "full")
        if preflight_errors:
            return jsonify({
                "success": False,
                "error": "恢复前检查未通过：\n" + "\n".join(f"• {e}" for e in preflight_errors),
                "preflight_errors": preflight_errors,
            }), 400
        state = PipelineOrchestrator.resume(pipeline_id)
        return jsonify({
            "success": True,
            "data": {
                "pipeline_id": state.pipeline_id,
                "task_id": state.task_id,
                "mode": state.mode,
                "status": state.status,
                "resumed": True,
            },
        })
    except FileNotFoundError:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.error(f"恢复管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@research_bp.route('/status/<pipeline_id>', methods=['GET'])
def pipeline_status(pipeline_id: str):
    """返回管线聚合进度（直接读 pipeline_state.json，可在后端重启后存活）。"""
    data = PipelineManager.load(pipeline_id)
    if data is None:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    return jsonify({"success": True, "data": data})


@research_bp.route('/list', methods=['GET'])
def list_pipelines():
    return jsonify({"success": True, "data": {"pipelines": PipelineManager.list_pipelines()}})


@research_bp.route('/<pipeline_id>/dossier', methods=['GET'])
def get_dossier(pipeline_id: str):
    """返回深度研究产出的研究报告 + 结构化 actors/sources。"""
    handoff = PipelineManager.handoff_dir(pipeline_id)
    if not os.path.isdir(handoff):
        return jsonify({"success": False, "error": "管线不存在"}), 404

    def _read(name):
        p = os.path.join(handoff, name)
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                return None
        return None

    import json as _json
    report = _read('research_report.md')
    actors_raw = _read('actors.json')
    sources_raw = _read('sources.json')
    return jsonify({
        "success": True,
        "data": {
            "report": report,
            "actors": _json.loads(actors_raw) if actors_raw else None,
            "sources": _json.loads(sources_raw) if sources_raw else None,
            "has_report": report is not None,
        },
    })


@research_bp.route('/<pipeline_id>/progress', methods=['GET'])
def get_progress_log(pipeline_id: str):
    """返回研究子进程进度日志的尾部（默认最后 200 行），用于前端控制台。"""
    handoff = PipelineManager.handoff_dir(pipeline_id)
    log_path = os.path.join(handoff, 'research_progress.log')
    if not os.path.exists(log_path):
        return jsonify({"success": True, "data": {"lines": []}})
    try:
        limit = int(request.args.get('lines', '200'))
    except ValueError:
        limit = 200
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        return jsonify({"success": True, "data": {"lines": lines[-limit:], "total": len(lines)}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
