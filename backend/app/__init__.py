"""
MiroFish Backend - Flask应用工厂
"""

import hmac
import json
import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, jsonify, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger
from .utils.security import redact_secrets


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 启动中...")
        logger.info("=" * 50)
    
    # 启用CORS —— 收敛到配置的来源白名单（EXECPLAN2 F-13-0），默认仅本机前端端口。
    # 设 APP_CORS_ORIGINS='*' 可恢复旧的全开行为。
    _cors_cfg = (Config.APP_CORS_ORIGINS or '').strip()
    if _cors_cfg == '*':
        _origins = "*"
    else:
        _origins = [o.strip() for o in _cors_cfg.split(',') if o.strip()] or ["http://localhost:3000"]
    CORS(app, resources={r"/api/*": {"origins": _origins}})
    
    # 进程生命周期挂钩会发送真实 OS 信号并改写 uploads 下的持久化状态。pytest
    # 构造 Flask app 只为测试路由，绝不能把另一个 localhost 后端拥有的活模拟误判为
    # 孤儿。测试 harness 在 conftest import 时设置 DRF_TEST_PROCESS；TESTING 配置是
    # 应用嵌入方的等价显式开关。生产进程仍保持原有注册/回收语义。
    _runtime_lifecycle_enabled = (
        not app.config.get('TESTING', False)
        and os.environ.get('DRF_TEST_PROCESS') != '1'
    )
    if _runtime_lifecycle_enabled:
        # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
        from .services.simulation_runner import SimulationRunner
        SimulationRunner.register_cleanup()
        # 回收上一进程遗留、仍在烧 LLM 额度的孤儿 OASIS 模拟进程（EXECPLAN2 F-12-0/F-6-5）。
        SimulationRunner.reconcile_orphans()
        if should_log_startup:
            logger.info("已注册模拟进程清理函数并回收孤儿模拟")

        # 统一管线（DeerFlow 研究 → 预测）的生命周期挂钩：
        #  1) 启动时回收上一个进程遗留、无活 owner 的孤儿管线；
        #  2) 注册关闭清理，终止在飞的 DeerFlow 研究子进程组（需放在
        #     SimulationRunner 之后，以便链式调用其信号处理器）。
        from .services.pipeline_orchestrator import PipelineOrchestrator
        PipelineOrchestrator.reconcile_orphans()
        PipelineOrchestrator.register_cleanup()
        if should_log_startup:
            logger.info("已注册研究管线清理函数并回收孤儿管线")
    elif should_log_startup:
        logger.info("测试进程：已禁用破坏性运行时注册与孤儿回收")
    
    # 鉴权/暴露面闸门（EXECPLAN2 F-13-0）：
    #   - 环回来源（本机前端经 vite 代理而来）一律放行，保持本地工作流不变；
    #   - 非环回来源：未配置 APP_API_TOKEN 时 fail-closed 拒绝；配置后需带正确的
    #     X-API-Token 头（常量时间比较）。/health 与 CORS 预检放行。
    @app.before_request
    def _auth_gate():
        if request.method == 'OPTIONS':
            return None
        path = request.path
        if path == '/health' or not path.startswith('/api/'):
            return None
        remote = request.remote_addr or ''
        if remote in ('127.0.0.1', '::1', 'localhost') or remote.startswith('127.'):
            return None
        token = Config.APP_API_TOKEN
        if not token:
            return jsonify({
                "success": False,
                "error": "forbidden: API is loopback-only unless APP_API_TOKEN is configured",
            }), 403
        supplied = request.headers.get('X-API-Token', '')
        if not hmac.compare_digest(supplied, token):
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return None

    # 请求日志中间件（敏感字段脱敏后才落盘，EXECPLAN2 F-13-1/F-8-0）
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            body = request.get_json(silent=True)
            logger.debug(f"请求体: {redact_secrets(body) if body is not None else body}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"响应: {response.status_code}")
        # 生产（非 DEBUG）下统一剥离错误响应里的 traceback 字段，避免泄露服务端
        # 文件路径/内部细节（EXECPLAN2 F-8-5）。这是覆盖全部蓝图 57 处反射的单一闸门。
        if not app.config.get('DEBUG') and response.is_json:
            try:
                data = response.get_json(silent=True)
                if isinstance(data, dict) and 'traceback' in data:
                    data.pop('traceback', None)
                    response.set_data(json.dumps(data, ensure_ascii=False))
            except Exception:
                pass
        return response
    
    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp, research_bp, settings_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(research_bp, url_prefix='/api/research')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')

    # 稳定版程序化 API 表面 /api/v1（EXECPLAN2 I-9-5）。可选降级：默认 **不注册**，
    # 仅当 Config.API_V1_ENABLED 为真时挂载，未开启则本机 SPA 与现有路由行为完全不变。
    # 鉴权/CORS 复用上面的 /api/* 统一闸门（/api/v1/* 自动继承）。
    if getattr(Config, 'API_V1_ENABLED', False):
        from .api import sdk_bp
        app.register_blueprint(sdk_bp, url_prefix='/api/v1')
        if should_log_startup:
            logger.info("已注册稳定版程序化 API：/api/v1（API_V1_ENABLED=true）")
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}
    
    if should_log_startup:
        logger.info("MiroFish Backend 启动完成")
    
    return app
