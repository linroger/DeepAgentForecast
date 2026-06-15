"""
API路由模块
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)
research_bp = Blueprint('research', __name__)
settings_bp = Blueprint('settings', __name__)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import research  # noqa: E402, F401
from . import settings  # noqa: E402, F401

# 稳定版程序化 API 表面 /api/v1（EXECPLAN2 I-9-5）。导入仅定义蓝图（无副作用，
# 不会把路由挂到 app 上）；实际注册由 create_app 在 Config.API_V1_ENABLED 时才执行。
from .sdk import sdk_bp  # noqa: E402, F401

