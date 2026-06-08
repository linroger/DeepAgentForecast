"""设置 API：运行时查询/切换 LLM 模型提供方。

路由（挂载于 /api/settings）：
    GET  /llm   当前提供方 + 受支持提供方清单（含展示元数据）
    POST /llm   切换提供方 {provider, api_key?, base_url?, model?}；对新发起的管线生效

切换是运行时的：更新 Config 类属性 + os.environ（DeerFlow 子进程继承），并 upsert 进 .env。
已在运行中的管线不受影响（它们已读取旧配置）。
"""

import traceback

from flask import jsonify, request

from . import settings_bp
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.settings')


@settings_bp.route('/llm', methods=['GET'])
def get_llm_settings():
    return jsonify({"success": True, "data": Config.provider_info()})


@settings_bp.route('/llm', methods=['POST'])
def set_llm_settings():
    try:
        data = request.get_json(silent=True) or {}
        provider = (data.get('provider') or '').strip().lower()
        if not provider:
            return jsonify({"success": False, "error": "缺少 provider"}), 400
        info = Config.apply_provider(
            provider,
            api_key=data.get('api_key'),
            base_url=data.get('base_url'),
            model=data.get('model'),
        )
        logger.info(f"LLM 提供方已切换为: {provider}（DeerFlow 研究模型={info.get('deerflow_model')}）")
        return jsonify({"success": True, "data": info})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"切换提供方失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500
