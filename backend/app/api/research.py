"""统一研究→预测管线 API (Step 0)

一个 prompt 进：DeerFlow 深度研究 → MiroFish 知识图谱 → OASIS 模拟 → 预测报告。

路由（挂载于 /api/research）：
    POST   /run                      启动管线，立即返回 {pipeline_id, task_id}
    POST   /<pipeline_id>/cancel     取消在飞管线（杀研究子进程 / 停 OASIS 模拟）
    POST   /<pipeline_id>/resume     从失败/取消的管线继续（复用已完成产物）
    DELETE /<pipeline_id>            删除已结束的管线记录（含 handoff 产物）
    POST   /clean                    批量清理失败/已取消的管线记录
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
from ..services.research_progress import merged_research_progress_tail
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.research')

_VALID_DEPTH = {"quick", "standard", "deep"}
_VALID_MODE = {"full", "research_only"}
# T5.5: 每次运行可选的研究语言（与 DeerFlow --target-language 对齐；auto=交给模型自选）
_VALID_LANGUAGES = {"Chinese", "English", "auto"}
# B2: 人工编辑档案时报告的最小字符门。与编排器 research_report>=400 复用守卫语义一致：
# <400 字符（含 LLM 降级/错误短串）一律拒绝，避免空/垃圾报告悄悄退化下游本体/图谱/模拟。
MIN_DOSSIER_CHARS = 400


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

        # T5.5: 每次运行可覆盖研究语言/模型（缺省回退 Config）。在任何子进程启动前校验，杜绝
        # 一个拼错的模型名烧完研究额度后才暴露。
        # research_language 是内部字段名（PipelineState.options/scheduled_rerun.py 用它）；
        # 接口契约字段是 language——同时接受两者，避免调用方按内部命名类比误传后静默退回默认语言。
        language = (data.get('language') or data.get('research_language') or '').strip() or None
        if language is not None and language not in _VALID_LANGUAGES:
            return jsonify({"success": False, "error": f"language 必须是 {sorted(_VALID_LANGUAGES)} 之一"}), 400
        if language == 'auto':
            language = ''  # 空串 → 研究子进程不传 --target-language，交给模型自选
        model = (data.get('model') or '').strip() or None
        if model is not None and model.lower() not in Config.SUPPORTED_DEERFLOW_MODELS:
            return jsonify({
                "success": False,
                "error": f"model 必须是 {', '.join(Config.SUPPORTED_DEERFLOW_MODELS)} 之一",
            }), 400
        if model:
            model = model.lower()

        # 起飞前体检：把"研究跑完 40 分钟后才发现 Zep Key 是占位符"这类失败提前到现在
        preflight_errors = preflight_pipeline(mode=mode, model=model)
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
            language=language,
            model=model,
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
        # ORCH-3: force=true 允许恢复一条 completed 但 pipeline_health 降级/失败的管线
        # （仅重置 REPORT 阶段重生成报告）；默认请求形状不变。
        _force = bool((request.get_json(silent=True) or {}).get("force"))
        state = PipelineOrchestrator.resume(pipeline_id, force=_force)
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


@research_bp.route('/<pipeline_id>/continue', methods=['POST'])
def continue_pipeline(pipeline_id: str):
    """T6.2: 把一条已完成的 research_only 管线继续跑成完整管线（复用研究产物，不重跑研究）。"""
    try:
        existing = PipelineManager.load(pipeline_id)
        if existing is None:
            return jsonify({"success": False, "error": "管线不存在"}), 404
        # 继续会跑完整管线（建图/模拟/报告），按 full 模式预检配置
        preflight_errors = preflight_pipeline(mode="full")
        if preflight_errors:
            return jsonify({
                "success": False,
                "error": "继续前检查未通过：\n" + "\n".join(f"• {e}" for e in preflight_errors),
                "preflight_errors": preflight_errors,
            }), 400
        state = PipelineOrchestrator.continue_to_full(pipeline_id)
        return jsonify({
            "success": True,
            "data": {
                "pipeline_id": state.pipeline_id,
                "task_id": state.task_id,
                "mode": state.mode,
                "status": state.status,
                "continued": True,
            },
        })
    except FileNotFoundError:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.error(f"继续管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@research_bp.route('/<pipeline_id>/scenario', methods=['POST'])
def fork_scenario(pipeline_id: str):
    """T4.6: 在 PREPARE 处分叉一个 what-if 情景管线（复用 base 研究/本体/图谱，仅重跑 prepare/run/report）。

    body: {label, max_rounds?, influence_overrides{name:weight}, stance_overrides{name:stance},
           injected_events[{round,poster_name,content}], as_of_shift?}
    """
    try:
        existing = PipelineManager.load(pipeline_id)
        if existing is None:
            return jsonify({"success": False, "error": "基础管线不存在"}), 404
        overlay = request.get_json(silent=True) or {}
        if not (overlay.get("label") or "").strip():
            return jsonify({"success": False, "error": "缺少情景标签 label"}), 400
        preflight_errors = preflight_pipeline(mode="full")
        if preflight_errors:
            return jsonify({
                "success": False,
                "error": "情景分叉前检查未通过：\n" + "\n".join(f"• {e}" for e in preflight_errors),
                "preflight_errors": preflight_errors,
            }), 400
        state = PipelineOrchestrator.fork(pipeline_id, overlay)
        return jsonify({
            "success": True,
            "data": {
                "pipeline_id": state.pipeline_id,
                "base_pipeline_id": pipeline_id,
                "task_id": state.task_id,
                "label": overlay.get("label"),
                "status": state.status,
            },
        })
    except FileNotFoundError:
        return jsonify({"success": False, "error": "基础管线不存在"}), 404
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.error(f"情景分叉失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@research_bp.route('/<pipeline_id>', methods=['DELETE'])
def delete_pipeline(pipeline_id: str):
    """删除一条已结束的管线记录（含 handoff 产物）。在飞管线须先取消。

    若有 fork（what-if 情景）依赖其共享 handoff 目录，默认拒绝（409 has_dependents）；
    传 ?force=true 可强制删除（会同时破坏这些 fork 的恢复/产物复用）。
    """
    try:
        force = str(request.args.get('force', '')).strip().lower() in ('1', 'true', 'yes')
        result = PipelineOrchestrator.delete_pipeline(pipeline_id, force=force)
        if result["status"] == "not_found":
            return jsonify({"success": False, "error": "管线不存在"}), 404
        if result["status"] == "still_running":
            return jsonify({"success": False, "error": "管线正在运行，请先取消再删除"}), 409
        if result["status"] == "has_dependents":
            deps = result.get("dependents", [])
            return jsonify({
                "success": False,
                "error": f"仍有 {len(deps)} 个 fork 依赖其 handoff 目录，请先删除这些 fork 或加 ?force=true 强制删除",
                "data": result,
            }), 409
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"删除管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@research_bp.route('/clean', methods=['POST'])
def clean_pipelines():
    """批量清理失败/已取消的管线记录。请求体可选 {statuses: ["failed","cancelled"]}。"""
    try:
        data = request.get_json(silent=True) or {}
        statuses = data.get('statuses') or ["failed", "cancelled"]
        if not isinstance(statuses, list) or not statuses:
            return jsonify({"success": False, "error": "statuses 必须是非空列表"}), 400
        allowed = {"failed", "cancelled"}
        bad = [s for s in statuses if s not in allowed]
        if bad:
            return jsonify({"success": False, "error": f"只允许清理终态 {sorted(allowed)}，收到 {bad}"}), 400
        result = PipelineOrchestrator.clean_terminal(tuple(statuses))
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"清理管线失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@research_bp.route('/status/<pipeline_id>', methods=['GET'])
def pipeline_status(pipeline_id: str):
    """返回管线聚合进度（直接读 pipeline_state.json，可在后端重启后存活）。"""
    data = PipelineManager.load(pipeline_id)
    if data is None:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    response = jsonify({"success": True, "data": data})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@research_bp.route('/list', methods=['GET'])
def list_pipelines():
    return jsonify({"success": True, "data": {"pipelines": PipelineManager.list_pipelines()}})


@research_bp.route('/preflight', methods=['GET'])
def preflight():
    """T5.6: 启动前就绪检查（不发起管线）。复用 POST /run 的同一套检查，避免漂移。

    EXECPLAN2 I-8-0: 默认行为不变（返回 {ready, errors, mode}）。可选 ?format=full
    走单一真源的 environment_report()（与 doctor.sh / preflight CLI 共用），返回带
    provider/deerflow_model/graph_backend + 结构化 checks[{id,severity,ok,message,fix}]
    的就绪文档，供前端设置页渲染实时就绪面板；?deep=true 追加 live 凭据/嵌入缓存/磁盘探针。
    """
    mode = request.args.get('mode', 'full')
    if mode not in ('full', 'research_only'):
        mode = 'full'

    if (request.args.get('format') or '').strip().lower() == 'full':
        # 复用 backend/scripts/preflight.py 的 environment_report()（同一引擎，零漂移）。
        # 容错降级：脚本不可导入时回退到旧版精简响应，绝不让设置页因此 500。
        try:
            import sys
            _scripts_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')
            _scripts_dir = os.path.abspath(_scripts_dir)
            if _scripts_dir not in sys.path:
                sys.path.insert(0, _scripts_dir)
            from preflight import environment_report  # type: ignore
            deep = (request.args.get('deep') or '').strip().lower() in ('1', 'true', 'yes')
            model = (request.args.get('model') or '').strip() or None
            report = environment_report(mode=mode, model=model, deep=deep)
            return jsonify({"success": True, "data": report})
        except Exception as e:
            logger.warning(f"preflight format=full 降级到精简响应: {e}")

    errors = preflight_pipeline(mode=mode)
    return jsonify({"success": True, "data": {"ready": not errors, "errors": errors, "mode": mode}})


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
    timeline_raw = _read('timeline.json')  # T5.2: 一等公民时间线
    quantitative_raw = _read('quantitative.json')
    contested_raw = _read('contested.json')
    markets_raw = _read('prediction_markets.json')
    charts_raw = _read('charts.json')

    def _decode(raw):
        try:
            return _json.loads(raw) if raw else None
        except (TypeError, ValueError):
            return None

    return jsonify({
        "success": True,
        "data": {
            "report": report,
            "actors": _decode(actors_raw),
            "sources": _decode(sources_raw),
            "timeline": _decode(timeline_raw),
            "quantitative": _decode(quantitative_raw),
            "contested": _decode(contested_raw),
            # Market signals are useful even when none are semantically exact
            # enough to become a model-vs-market forecast anchor.  Surface the
            # canonical status rather than hiding the entire lane behind the
            # final report's comparison matcher.
            "prediction_markets": _decode(markets_raw),
            "charts": _decode(charts_raw),
            "has_report": report is not None,
        },
    })


@research_bp.route('/<pipeline_id>/dossier', methods=['PUT'])
def edit_dossier(pipeline_id: str):
    """T5.4: 编辑研究档案（research_report.md / actors.json），人类介入后再继续。

    仅当 status==completed && mode==research_only（或失败于建图之前）时允许，避免改动已下游消费的档案。
    原子写（tmp + os.replace）。body: {report?: str, actors?: object}。
    """
    import json as _json
    data = PipelineManager.load(pipeline_id)
    if data is None:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    status = data.get("status")
    mode = data.get("mode")
    graph_done = bool((data.get("stages") or {}).get("graph", {}).get("status") == "completed")
    # 允许：已完成的 research_only；或失败/取消且尚未建图（编辑后可继续）
    allowed = (status == "completed" and mode == "research_only") or (
        status in ("failed", "cancelled") and not graph_done
    )
    if not allowed:
        return jsonify({"success": False, "error": "当前状态不允许编辑档案（仅完成的 research_only 或建图前的失败管线可编辑）"}), 409

    body = request.get_json(silent=True) or {}
    handoff = PipelineManager.handoff_dir(pipeline_id)
    if not os.path.isdir(handoff):
        return jsonify({"success": False, "error": "管线产物目录不存在"}), 404

    # B2: 写入前质量门。若提交了 report，去空白后须 >= MIN_DOSSIER_CHARS，否则硬拒绝（400），
    # 杜绝空/过短报告被原子写入后悄悄退化下游本体/图谱/模拟。actors/sources 为可选，缺失不拦。
    if isinstance(body.get("report"), str):
        if len(body["report"].strip()) < MIN_DOSSIER_CHARS:
            return jsonify({
                "success": False,
                "error": (
                    f"研究报告过短（去空白后须 >= {MIN_DOSSIER_CHARS} 字符），拒绝写入以免退化下游。"
                    f" Research report too short (must be >= {MIN_DOSSIER_CHARS} chars after trimming)."
                ),
            }), 400

    def _atomic_write(name, text):
        path = os.path.join(handoff, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    wrote = []
    try:
        if isinstance(body.get("report"), str):
            _atomic_write("research_report.md", body["report"])
            wrote.append("research_report.md")
        if body.get("actors") is not None:
            _atomic_write("actors.json", _json.dumps(body["actors"], ensure_ascii=False, indent=2))
            wrote.append("actors.json")
    except Exception as e:
        logger.error(f"编辑档案写入失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
    if not wrote:
        return jsonify({"success": False, "error": "未提供 report 或 actors"}), 400
    return jsonify({"success": True, "data": {"updated": wrote}})


# T6.3: 各阶段产物深链。artifacts 映射在 pipeline_state.json 里维护（_record_stage_artifacts）：
# dossier/timeline/sources/ontology/communities/initial_posts/personas/run_summary。
@research_bp.route('/<pipeline_id>/artifact/<name>', methods=['GET'])
def get_artifact(pipeline_id: str, name: str):
    """T6.3: 返回某阶段产物（JSON 解析后内容；csv/未知按纯文本）。"""
    data = PipelineManager.load(pipeline_id)
    if data is None:
        return jsonify({"success": False, "error": "管线不存在"}), 404
    artifacts = (data.get("artifacts") or {})
    path = artifacts.get(name)
    # Live scans store provisional assets as <name>_partial; chart URLs stay
    # stable before and after stage completion by accepting that pointer until
    # the completion boundary registers the formal key.
    if not path and name.startswith("chart_"):
        path = artifacts.get(f"{name}_partial")
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "error": f"产物 '{name}' 不存在"}), 404
    try:
        # GATE-W9：二进制图表产物（VIZ-2 登记的 chart_*.png / *.svg）按原始字节 + 正确
        # mimetype 直出——此前统一按 utf-8 文本读取，PNG 解码即抛 500，注册了也取不到。
        _ext = os.path.splitext(path)[1].lower()
        _asset_mime = {
            '.png': 'image/png',
            '.svg': 'image/svg+xml',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.html': 'text/html; charset=utf-8',
        }.get(_ext)
        if _asset_mime:
            if name.startswith("chart_"):
                # Revalidate the persisted pointer at serve time. A renderer can
                # replace a once-regular chart with a symlink after a live scan;
                # formal/partial registration is therefore not a trust boundary.
                handoff = os.path.realpath(PipelineManager.handoff_dir(pipeline_id))
                charts_root = os.path.realpath(os.path.join(handoff, "charts"))
                resolved = os.path.realpath(path)
                expected_file = name[len("chart_"):]
                if (os.path.basename(path) != expected_file
                        or os.path.basename(resolved) != expected_file
                        or os.path.islink(path)
                        or not os.path.isfile(resolved)
                        or os.path.commonpath([resolved, charts_root]) != charts_root):
                    return jsonify({
                        "success": False,
                        "error": f"产物 '{name}' 不存在",
                    }), 404
                path = resolved
            from flask import send_file
            response = send_file(path, mimetype=_asset_mime)
            if _ext == '.html':
                response.headers["Content-Security-Policy"] = (
                    "sandbox allow-scripts; default-src 'none'; "
                    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "img-src data: blob:; font-src data:; connect-src 'none'"
                )
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
            elif _ext == '.svg':
                response.headers["Content-Security-Policy"] = (
                    "sandbox; default-src 'none'; script-src 'none'; "
                    "style-src 'unsafe-inline'; img-src data:; font-src data:"
                )
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
            return response
        with open(path, 'r', encoding='utf-8') as f:
            raw = f.read()
        import json as _json
        if path.endswith('.json'):
            return jsonify({"success": True, "data": {"name": name, "content": _json.loads(raw)}})
        return jsonify({"success": True, "data": {"name": name, "content": raw}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@research_bp.route('/<pipeline_id>/progress', methods=['GET'])
def get_progress_log(pipeline_id: str):
    """返回研究子进程进度日志的实时合并尾部，用于前端控制台。

    并行研究时 producer 写在 ``handoff/track_N/research_progress.log``；根目录日志直到
    单轨/收尾路径才可能存在。每个文件只读有界尾部，再按 ISO 时间戳合并、去重并截断，
    因此轮询不会随数小时运行产生的多 MB 日志线性变慢。
    """
    handoff = PipelineManager.handoff_dir(pipeline_id)
    try:
        limit = int(request.args.get('lines', '200'))
    except ValueError:
        limit = 200
    try:
        tail = merged_research_progress_tail(handoff, limit)
        response = jsonify({"success": True, "data": {
            "lines": tail.lines,
            "returned": len(tail.lines),
            # Exact historical line counts require an O(file) scan and are
            # deliberately unavailable on this live bounded endpoint.
            "total": None,
            "total_exact": False,
            "source_count": tail.source_count,
            "truncated": tail.truncated,
        }})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
