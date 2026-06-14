"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    # 默认关闭 debug：开发期显式设 FLASK_DEBUG=true。debug 模式有两个生产隐患——
    # (1) Werkzeug 调试器暴露在 0.0.0.0（局域网可触发任意代码执行）；
    # (2) 自动 reloader 会在代码变动时重启进程，杀死在飞的研究/模拟管线。
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False

    # —— API 暴露面收敛（EXECPLAN2 F-13-0 / F-13-2）——
    # 默认仅环回可达：未配置令牌时，非环回来源一律拒绝（fail-closed）。
    # 配置 APP_API_TOKEN 后，所有 /api/* 变更请求需带 X-API-Token 头（常量时间比较）。
    APP_API_TOKEN = os.environ.get('APP_API_TOKEN', '').strip()
    # 允许的 CORS 来源（逗号分隔）。默认仅本机前端开发端口；设为 '*' 可恢复旧的全开行为。
    APP_CORS_ORIGINS = os.environ.get(
        'APP_CORS_ORIGINS',
        'http://localhost:3000,http://127.0.0.1:3000,http://localhost:5001,http://127.0.0.1:5001',
    ).strip()
    # 连通性/研究子进程发起的出站请求是否禁止私网/环回地址（暴露到环回之外时建议开启）。
    APP_BLOCK_PRIVATE_URLS = os.environ.get('APP_BLOCK_PRIVATE_URLS', 'False').strip().lower() == 'true'
    
    # LLM提供方（默认使用 Claude Code CLI 订阅）
    # claude-cli: 通过本机 `claude` CLI 调用（使用 Claude Code 订阅，无需 API Key）
    # codex-cli:  通过本机 `codex` CLI 调用（使用 Codex 订阅，无需 API Key）
    # openai:     回退到 OpenAI 兼容 API（需要 LLM_API_KEY）
    # kimi:       Kimi-for-coding（api.kimi.com/coding，OpenAI 兼容 + coding-agent UA 网关）
    # minimax:    MiniMax 代码计划（api.minimaxi.com 国内版，OpenAI 兼容，MiniMax-M3 推理模型）
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'claude-cli').strip().lower()

    # Kimi-for-coding 默认连接参数（provider=kimi 且未显式覆盖时启用）
    _KIMI_DEFAULT_BASE_URL = 'https://api.kimi.com/coding/v1'
    _KIMI_DEFAULT_MODEL = 'kimi-for-coding'
    _is_kimi = LLM_PROVIDER == 'kimi'

    # MiniMax 代码计划默认连接参数（provider=minimax 且未显式覆盖时启用）
    # 国内版 OpenAI 兼容端点；模型名严格区分大小写 'MiniMax-M3'（512K 上下文）。
    _MINIMAX_DEFAULT_BASE_URL = 'https://api.minimaxi.com/v1'
    _MINIMAX_DEFAULT_MODEL = 'MiniMax-M3'
    _is_minimax = LLM_PROVIDER == 'minimax'

    # —— 新增 OpenAI 兼容提供方默认连接参数 ——
    # DeepSeek V4（api.deepseek.com，1M 上下文）。报告/模拟为高频调用，默认用更经济稳定的
    # deepseek-chat；深度研究阶段在 deer-flow/config.yaml 用旗舰 deepseek-v4-pro。
    _DEEPSEEK_DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
    _DEEPSEEK_DEFAULT_MODEL = 'deepseek-chat'
    _is_deepseek = LLM_PROVIDER == 'deepseek'
    # 通义千问 Qwen（DashScope OpenAI 兼容；国际站端点，CN 用户改 dashscope.aliyuncs.com）。
    _QWEN_DEFAULT_BASE_URL = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
    _QWEN_DEFAULT_MODEL = 'qwen-plus'
    _is_qwen = LLM_PROVIDER == 'qwen'
    # 智谱 GLM（Z.ai/BigModel OpenAI 兼容；国际站端点，CN 用户改 open.bigmodel.cn）。
    _GLM_DEFAULT_BASE_URL = 'https://api.z.ai/api/paas/v4'
    _GLM_DEFAULT_MODEL = 'glm-4.6'
    _is_glm = LLM_PROVIDER == 'glm'

    # LLM配置（provider=openai/kimi/minimax/deepseek/qwen/glm 时统一使用 OpenAI 格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL') or (
        _KIMI_DEFAULT_BASE_URL if _is_kimi
        else _MINIMAX_DEFAULT_BASE_URL if _is_minimax
        else _DEEPSEEK_DEFAULT_BASE_URL if _is_deepseek
        else _QWEN_DEFAULT_BASE_URL if _is_qwen
        else _GLM_DEFAULT_BASE_URL if _is_glm
        else 'https://api.openai.com/v1'
    )
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME') or (
        _KIMI_DEFAULT_MODEL if _is_kimi
        else _MINIMAX_DEFAULT_MODEL if _is_minimax
        else _DEEPSEEK_DEFAULT_MODEL if _is_deepseek
        else _QWEN_DEFAULT_MODEL if _is_qwen
        else _GLM_DEFAULT_MODEL if _is_glm
        else 'gpt-4o-mini'
    )

    # coding-agent 身份头：Kimi-for-coding 网关按 User-Agent 校验调用方，
    # 必须发送被识别的 coding-agent UA（如 claude-cli/...）否则返回 access_terminated_error。
    # 注意：MiniMax 不做 UA 校验，仅 kimi 需要注入此头。
    LLM_USER_AGENT = os.environ.get('LLM_USER_AGENT', 'claude-cli/1.0.0')

    # kimi-for-coding / MiniMax-M3 都是“推理模型”：默认会把大量 token 花在隐藏推理上，
    # 当 max_tokens 被推理耗尽时返回的 content 为空（finish_reason=length），导致 JSON 解析失败；
    # MiniMax 还会把思维链以 <think>…</think> 内联进 content。对模拟/报告这类多调用、要求稳定
    # 可解析输出的工作负载，默认关闭推理（thinking:disabled）——content 直出、更快更省。
    # 可设对应 *_DISABLE_THINKING=false 重新开启推理。
    LLM_KIMI_DISABLE_THINKING = os.environ.get('LLM_KIMI_DISABLE_THINKING', 'true').strip().lower() == 'true'
    LLM_MINIMAX_DISABLE_THINKING = os.environ.get('LLM_MINIMAX_DISABLE_THINKING', 'true').strip().lower() == 'true'
    # 新增推理提供方(deepseek/qwen/glm)的统一关闭推理开关（默认关闭推理，保证报告/模拟输出稳定可解析）
    LLM_DISABLE_THINKING = os.environ.get('LLM_DISABLE_THINKING', 'true').strip().lower() == 'true'

    # 各推理提供方"关闭推理"对应的 extra_body。GLM/DeepSeek/Kimi/MiniMax 用 thinking.type；
    # 通义千问 DashScope 用非标准的 enable_thinking 布尔。
    _DISABLE_THINKING_EXTRA_BODY = {
        'kimi': {"thinking": {"type": "disabled"}},
        'minimax': {"thinking": {"type": "disabled"}},
        'deepseek': {"thinking": {"type": "disabled"}},
        'glm': {"thinking": {"type": "disabled"}},
        'qwen': {"enable_thinking": False},
    }

    @classmethod
    def reasoning_extra_body(cls):
        """OpenAI 兼容推理提供方关闭推理用的 extra_body；非推理提供方或开关关闭时返回 None。

        kimi/minimax 沿用各自历史开关；deepseek/qwen/glm 统一受 LLM_DISABLE_THINKING 控制。
        关闭推理可避免 reasoning 吃光 max_tokens 导致 content 为空、JSON 解析失败。
        """
        p = cls.LLM_PROVIDER
        if p == 'kimi' and not cls.LLM_KIMI_DISABLE_THINKING:
            return None
        if p == 'minimax' and not cls.LLM_MINIMAX_DISABLE_THINKING:
            return None
        if p in ('deepseek', 'qwen', 'glm') and not cls.LLM_DISABLE_THINKING:
            return None
        return cls._DISABLE_THINKING_EXTRA_BODY.get(p)

    # 向后兼容别名（旧调用点）：等价于 reasoning_extra_body。
    @classmethod
    def kimi_extra_body(cls):
        return cls.reasoning_extra_body()

    # —— 运行时模型提供方切换（供 /api/settings 使用）——
    # 每个提供方的展示元数据：是否需要 API Key、对应的 DeerFlow 研究模型（deer-flow/config.yaml
    # 当前仅定义了 claude + minimax 两个研究模型，故其余提供方的深度研究回退到 claude/OAuth）。
    # 每个提供方的展示与路由元数据：
    #   label          前端显示名
    #   needs_key      是否需要用户填写 API Key（CLI/订阅类为 False）
    #   deerflow_model 深度研究阶段在 deer-flow/config.yaml 选用的模型 stanza 名
    #   openai_compat  报告/模拟阶段是否走 OpenAI 兼容 HTTP 客户端（否则走本机 CLI）
    #   default_base / default_model  OpenAI 兼容提供方的默认连接参数
    #   key_env        把用户填的 Key 镜像到的提供方专属环境变量（供 deer-flow $VAR 解析）
    PROVIDER_META = {
        'claude-cli': {'label': 'Claude Code（CLI 订阅）', 'needs_key': False, 'deerflow_model': 'claude', 'openai_compat': False},
        'codex-cli':  {'label': 'Codex（ChatGPT 订阅）',   'needs_key': False, 'deerflow_model': 'codex',  'openai_compat': False},
        'openai':     {'label': 'OpenAI 兼容 API',          'needs_key': True,  'deerflow_model': 'claude', 'openai_compat': True,
                       'default_base': 'https://api.openai.com/v1', 'default_model': 'gpt-4o-mini'},
        'kimi':       {'label': 'Kimi-for-coding',          'needs_key': True,  'deerflow_model': 'kimi', 'openai_compat': True,
                       'default_base': _KIMI_DEFAULT_BASE_URL, 'default_model': _KIMI_DEFAULT_MODEL, 'key_env': 'KIMI_API_KEY'},
        'minimax':    {'label': 'MiniMax 代码计划（国内版）', 'needs_key': True,  'deerflow_model': 'minimax', 'openai_compat': True,
                       'default_base': _MINIMAX_DEFAULT_BASE_URL, 'default_model': _MINIMAX_DEFAULT_MODEL, 'key_env': 'MINIMAX_API_KEY'},
        'deepseek':   {'label': 'DeepSeek V4',              'needs_key': True,  'deerflow_model': 'deepseek', 'openai_compat': True,
                       'default_base': _DEEPSEEK_DEFAULT_BASE_URL, 'default_model': _DEEPSEEK_DEFAULT_MODEL, 'key_env': 'DEEPSEEK_API_KEY'},
        'qwen':       {'label': '通义千问 Qwen3.7 Max',      'needs_key': True,  'deerflow_model': 'qwen', 'openai_compat': True,
                       'default_base': _QWEN_DEFAULT_BASE_URL, 'default_model': _QWEN_DEFAULT_MODEL, 'key_env': 'DASHSCOPE_API_KEY'},
        'glm':        {'label': '智谱 GLM-4.6',             'needs_key': True,  'deerflow_model': 'glm', 'openai_compat': True,
                       'default_base': _GLM_DEFAULT_BASE_URL, 'default_model': _GLM_DEFAULT_MODEL, 'key_env': 'ZHIPUAI_API_KEY'},
    }

    @classmethod
    def provider_info(cls):
        """当前提供方 + 受支持提供方清单（含展示元数据），供前端设置菜单渲染。"""
        return {
            'current': cls.LLM_PROVIDER,
            'deerflow_model': cls.DEERFLOW_MODEL,
            'has_api_key': bool(cls.LLM_API_KEY),
            'base_url': cls.LLM_BASE_URL,
            'model_name': cls.LLM_MODEL_NAME,
            'providers': [
                {'id': pid, 'label': meta['label'], 'needs_key': meta['needs_key']}
                for pid, meta in cls.PROVIDER_META.items()
            ],
        }

    @classmethod
    def apply_provider(cls, provider, api_key=None, base_url=None, model=None):
        """在运行时切换 LLM 提供方（对**新发起**的管线生效，无需重启）。

        更新 Config 类属性 + os.environ（DeerFlow 子进程继承环境变量），并持久化到 .env。
        OpenAI 兼容提供方(openai/kimi/minimax)未显式传 base_url/model 时回退到各自默认值。
        """
        provider = (provider or '').strip().lower()
        if provider not in cls.SUPPORTED_LLM_PROVIDERS:
            raise ValueError(f"不支持的提供方: {provider}（需为 {', '.join(cls.SUPPORTED_LLM_PROVIDERS)} 之一）")
        meta = cls.PROVIDER_META.get(provider, {})
        is_openai_compat = bool(meta.get('openai_compat'))

        keeps_existing_key = (provider == cls.LLM_PROVIDER and bool(cls.LLM_API_KEY))
        if meta.get('needs_key') and not ((api_key or '').strip() or keeps_existing_key):
            raise ValueError(f"提供方 {provider} 需要 API Key")

        # 校验/清洗用户输入，避免 .env 注入与 SSRF（EXECPLAN2 F-8-1 / F-13-2）。
        from .utils.security import sanitize_env_value, validate_safe_url
        try:
            api_key = sanitize_env_value(api_key) if api_key else api_key
            model = sanitize_env_value(model) if model else model
            base_url = sanitize_env_value(base_url) if base_url else base_url
        except ValueError as e:
            raise ValueError(f"非法字段（含换行/控制字符）：{e}")
        if is_openai_compat and base_url:
            try:
                validate_safe_url(base_url, block_private=cls.APP_BLOCK_PRIVATE_URLS)
            except ValueError as e:
                raise ValueError(f"非法的 base_url：{e}")

        cls.LLM_PROVIDER = provider
        cls._is_kimi = provider == 'kimi'
        cls._is_minimax = provider == 'minimax'
        cls._is_deepseek = provider == 'deepseek'
        cls._is_qwen = provider == 'qwen'
        cls._is_glm = provider == 'glm'
        cls.DEERFLOW_MODEL = meta.get('deerflow_model', 'claude')

        env_updates = {'LLM_PROVIDER': provider, 'DEERFLOW_MODEL': cls.DEERFLOW_MODEL}
        if is_openai_compat:
            cls.LLM_BASE_URL = (base_url or '').strip() or meta.get('default_base') or 'https://api.openai.com/v1'
            cls.LLM_MODEL_NAME = (model or '').strip() or meta.get('default_model') or 'gpt-4o-mini'
            _key = (api_key or '').strip()
            if _key:
                cls.LLM_API_KEY = _key
            env_updates['LLM_BASE_URL'] = cls.LLM_BASE_URL
            env_updates['LLM_MODEL_NAME'] = cls.LLM_MODEL_NAME
            if cls.LLM_API_KEY:
                env_updates['LLM_API_KEY'] = cls.LLM_API_KEY
                # 把 Key 镜像到提供方专属环境变量，供 deer-flow/config.yaml 的 $VAR 解析
                # （deepseek→$DEEPSEEK_API_KEY、qwen→$DASHSCOPE_API_KEY、glm→$ZHIPUAI_API_KEY、
                #  minimax→$MINIMAX_API_KEY），这样深度研究子进程也能拿到正确的 Key。
                key_env = meta.get('key_env')
                if key_env:
                    env_updates[key_env] = cls.LLM_API_KEY

        for k, v in env_updates.items():
            os.environ[k] = v
        cls._persist_env(env_updates)
        return cls.provider_info()

    @classmethod
    def _persist_env(cls, updates):
        """把 key=value 安全 upsert 进项目根 .env（best-effort，失败不抛）。

        每个值都经 sanitize（拒绝换行/控制字符，防止注入额外 KEY=VALUE 行）+ dotenv
        安全引号，再原子落盘（EXECPLAN2 F-8-1）。
        """
        try:
            from .utils.security import sanitize_env_value, quote_env_value
            from .utils.atomic import write_text_atomic
            env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env'))
            lines = []
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    lines = f.read().splitlines()
            # 预先清洗所有值；任一非法直接放弃整次写入（不破坏现有 .env）。
            safe = {k: quote_env_value(sanitize_env_value(v)) for k, v in updates.items()}
            remaining = dict(safe)
            out = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    key = stripped.split('=', 1)[0].strip()
                    if key in remaining:
                        out.append(f"{key}={remaining.pop(key)}")
                        continue
                out.append(line)
            for key, val in remaining.items():
                out.append(f"{key}={val}")
            write_text_atomic(env_path, '\n'.join(out) + '\n')
        except Exception:
            pass

    # ============================================================
    # 知识图谱后端：本地 Graphiti（替代 Zep Cloud）
    # 不再需要 ZEP_API_KEY / 任何外部 SaaS。图谱在本机运行：
    #   - 默认嵌入式 FalkorDB（falkordblite，Python>=3.12，无需 Docker）
    #   - 实体/关系抽取复用 Config.LLM_PROVIDER（含 claude-cli 等免 Key 提供方）
    #   - 向量嵌入用本地 sentence-transformers 多语言模型（无需 Key）
    # ============================================================
    # 图数据库后端：auto | falkordblite | falkordb | kuzu
    GRAPH_BACKEND = os.environ.get('GRAPH_BACKEND', 'auto').strip().lower()
    # 图数据持久化目录（嵌入式 FalkorDB / Kuzu 文件落盘位置）
    GRAPHITI_DATA_DIR = os.environ.get(
        'GRAPHITI_DATA_DIR',
        os.path.join(os.path.dirname(__file__), '../uploads/graphiti_db')
    )
    # 本地嵌入模型（多语言，覆盖中英文舆情内容）及其维度
    GRAPHITI_EMBED_MODEL = os.environ.get('GRAPHITI_EMBED_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
    GRAPHITI_EMBED_DIM = int(os.environ.get('GRAPHITI_EMBED_DIM', '384'))
    # 重排序：rrf（默认，纯本地、零下载）| bge（本地 sentence-transformers 交叉编码器，更准但需下载模型）
    GRAPHITI_RERANKER = os.environ.get('GRAPHITI_RERANKER', 'rrf').strip().lower()
    # 可选：连接外部 FalkorDB 服务（设置后 auto 优先使用）
    FALKORDB_HOST = os.environ.get('FALKORDB_HOST') or None
    FALKORDB_PORT = int(os.environ.get('FALKORDB_PORT', '6379'))

    # 兼容保留：旧版 Zep 配置（已不再必需）。本地 Graphiti 不需要任何 Key，但代码中仍有若干
    # `if not Config.ZEP_API_KEY` / `if not self.api_key` 真值守卫与服务构造检查。给一个非空哨兵值
    # 让这些守卫一律通过（shim 会忽略该值），从而无需改动 5 个服务构造器与 API 守卫。
    # 四个重试/退避旋钮仍被分页工具复用于本地图谱的瞬态错误重试。
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY') or 'local-graphiti'
    ZEP_MAX_RETRIES = int(os.environ.get('ZEP_MAX_RETRIES', '4'))
    ZEP_RETRY_DELAY_SECONDS = float(os.environ.get('ZEP_RETRY_DELAY_SECONDS', '2.0'))
    ZEP_RATE_LIMIT_BUFFER_SECONDS = float(os.environ.get('ZEP_RATE_LIMIT_BUFFER_SECONDS', '1.0'))
    ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS = float(os.environ.get('ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS', '90.0'))
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    # T3.7: 0 = 不截断（跑满按 total_hours/minutes_per_round 算出的完整轮数，如 72h/60min=72 轮）。
    # 设为正整数则作为全局轮数上限（每次运行可被 options.max_rounds 覆盖；冒烟测试用小值）。
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '0'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    # T4.4: 默认 8 = 与原硬编码 MAX_TOOL_CALLS_PER_SECTION 一致（接入 Config 后行为不变）。
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '8'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    # 支持的 LLM 提供方（直接从 PROVIDER_META 派生，新增提供方只需改一处）
    SUPPORTED_LLM_PROVIDERS = tuple(PROVIDER_META.keys())

    # ============================================================
    # DeerFlow 深度研究集成（前置 Step 0：用一个 prompt 自动调研生成种子材料）
    # DeerFlow runs in its OWN venv (separate dependency tree). MiroFish launches
    # the in-repo deer-flow checkout's deerflow_research.py via subprocess and
    # consumes the file-based handoff contract.
    # ============================================================
    # 默认指向仓库内的 deer-flow 目录（由 ./setup.sh 自动下载）：<repo>/deer-flow
    DEERFLOW_DIR = os.environ.get(
        'DEERFLOW_DIR',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '../..', 'deer-flow'))
    )
    # DeerFlow venv 的 python（留空则自动探测 .venv，再退回到 `uv run`）
    DEERFLOW_PYTHON = os.environ.get('DEERFLOW_PYTHON', '').strip() or None
    # DeerFlow config.yaml 中的模型名（默认 claude → Claude Code 订阅 OAuth；
    # 可选 claude | minimax | deepseek | qwen | glm | codex | kimi）
    DEERFLOW_MODEL = os.environ.get('DEERFLOW_MODEL', 'claude').strip()
    # 研究深度：quick / standard / deep
    DEERFLOW_RESEARCH_DEPTH = os.environ.get('DEERFLOW_RESEARCH_DEPTH', 'standard').strip().lower()
    # 研究报告/结构化输出语言（MiroFish 面向中文舆论，默认中文；留空交给模型自选）
    DEERFLOW_RESEARCH_LANGUAGE = os.environ.get('DEERFLOW_RESEARCH_LANGUAGE', 'Chinese').strip() or None
    # 研究阶段最长等待秒数（仅作为兜底/显式覆盖；正常由研究深度自适应）
    DEERFLOW_RESEARCH_TIMEOUT = int(os.environ.get('DEERFLOW_RESEARCH_TIMEOUT', '10800'))
    # 是否启用 DeerFlow 子代理（并行 scoped workers，更深但更慢）
    DEERFLOW_SUBAGENTS = os.environ.get('DEERFLOW_SUBAGENTS', 'false').strip().lower() == 'true'

    # ============================================================
    # EXECPLAN —— 打通「研究 → 图谱 → 模拟 → 报告」结构化契约的旋钮
    # 默认值保持「当前行为」；所有新字段可选降级（缺失即回退旧路径）。
    # ============================================================
    # --- 图谱（Phase 2）---
    # 文本抽取前，把研究确认的 actors + relationships 作为 typed 边种入图谱（T2.2）
    GRAPH_SEED_FROM_ACTORS = os.environ.get('GRAPH_SEED_FROM_ACTORS', 'true').strip().lower() == 'true'
    # episode 并发抽取数（>1 提速，但有轻微 dedup 排序风险；1 = 与旧行为逐字节一致）(T2.5)
    GRAPH_BUILD_CONCURRENCY = int(os.environ.get('GRAPH_BUILD_CONCURRENCY', '1'))
    # 建图末尾跑 Leiden 社区发现（派系/联盟，best-effort，失败不影响建图）(T2.4)
    GRAPH_BUILD_COMMUNITIES = os.environ.get('GRAPH_BUILD_COMMUNITIES', 'false').strip().lower() == 'true'
    # 远程 Graphiti/Zep 才需要分批限流停顿；本地 FalkorDB 关闭死延迟（T2.6）
    GRAPHITI_REMOTE = os.environ.get('GRAPHITI_REMOTE', 'false').strip().lower() == 'true'

    # --- 模拟（Phase 3）---
    # 智能体数量上限；超过则按 (是否匹配 actor, 影响力, 邻边数) 排序保留，始终保留研究 actor（T3.13）
    OASIS_MAX_AGENTS = int(os.environ.get('OASIS_MAX_AGENTS', '80'))
    # 模拟 → 图谱反馈回路（本地默认开；写回模拟期间涌现的关系，报告阶段可见）(T3.10)
    SIM_GRAPH_FEEDBACK = os.environ.get('SIM_GRAPH_FEEDBACK', 'true').strip().lower() == 'true'
    # 反馈除自由文本 episode 外，再写带名实体的 typed 边（A LIKED/REPLIED_TO/FOLLOWED B）(T3.10)
    SIM_TYPED_FEEDBACK_EDGES = os.environ.get('SIM_TYPED_FEEDBACK_EDGES', 'true').strip().lower() == 'true'
    # 把 *_config 的 recsys 旋钮（recsys_type/refresh_rec_post_count/max_rec_post_len + echo→
    # following_post_count）映射到 oasis Platform；默认关 = 用 DefaultPlatformType（与旧行为一致）(T3.12)
    SIM_WIRE_RECSYS = os.environ.get('SIM_WIRE_RECSYS', 'false').strip().lower() == 'true'

    # --- 报告（Phase 4）---
    # 每节最少/对话模式最多工具调用（与 REPORT_AGENT_MAX_TOOL_CALLS 配套；T4.4 接入硬编码值）
    REPORT_AGENT_MIN_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MIN_TOOL_CALLS', '4'))
    REPORT_AGENT_MAX_TOOL_CALLS_CHAT = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS_CHAT', '2'))
    # 用 DeerFlow ClaudeChatModel 的原生 tool calling 取代手搓 ReAct（仅 claude；默认关，最后启用）(T4.5)
    REPORT_NATIVE_TOOLS = os.environ.get('REPORT_NATIVE_TOOLS', 'false').strip().lower() == 'true'

    # --- DeerFlow 模型 / Key / 预算 单一真源（T6.4 / T6.6）---
    SUPPORTED_DEERFLOW_MODELS = ('claude', 'codex', 'minimax', 'deepseek', 'qwen', 'glm', 'kimi')
    # 模型 → 所需 Key 环境变量（claude/codex 用本机订阅，无需 Key）
    DEERFLOW_KEY_ENV = {
        'minimax': 'MINIMAX_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY',
        'qwen': 'DASHSCOPE_API_KEY', 'glm': 'ZHIPUAI_API_KEY', 'kimi': 'KIMI_API_KEY',
    }
    # 研究深度 → 超时预算（秒）；DEERFLOW_RESEARCH_TIMEOUT 为显式覆盖（优先级最高）(T6.6)
    DEERFLOW_DEPTH_BUDGETS = {'quick': 900, 'standard': 2400, 'deep': 10800}
    # deep 开场 pass 的递归上限（旧版在 bridge 内直接读 os.environ；提升为 Config 属性）(T6.6)
    DEERFLOW_DEEP_OPENING_RECURSION_LIMIT = int(os.environ.get('DEERFLOW_DEEP_OPENING_RECURSION_LIMIT', '220'))

    # 统一管线产物目录
    PIPELINE_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/pipelines')

    # .env.example 里的占位符：保留占位符等同于未配置（否则首跑要等研究阶段
    # 烧完几十分钟额度后才在建图阶段发现 Zep 401）。
    _PLACEHOLDER_VALUES = {
        'your_zep_api_key_here', 'your_zep_api_key', 'your_api_key',
        'your_api_key_here', 'changeme', 'xxx', '...',
    }

    @classmethod
    def _is_placeholder(cls, value) -> bool:
        return bool(value) and str(value).strip().lower() in cls._PLACEHOLDER_VALUES

    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []

        if cls.LLM_PROVIDER not in cls.SUPPORTED_LLM_PROVIDERS:
            errors.append(
                f"LLM_PROVIDER 必须是 {', '.join(cls.SUPPORTED_LLM_PROVIDERS)} 之一，"
                f"当前为 '{cls.LLM_PROVIDER}'"
            )

        # 需要 Key 的提供方（needs_key=True）必须配置 LLM_API_KEY；CLI/订阅提供方使用本机订阅
        if cls.PROVIDER_META.get(cls.LLM_PROVIDER, {}).get('needs_key') and not cls.LLM_API_KEY:
            errors.append(f"LLM_PROVIDER={cls.LLM_PROVIDER} 时必须配置 LLM_API_KEY")

        # 知识图谱已迁移到本地 Graphiti——不再需要 ZEP_API_KEY。
        # 仅校验 GRAPH_BACKEND 取值合法；嵌入式后端无需任何外部服务或 Key。
        _valid_backends = ('auto', 'falkordblite', 'falkordb', 'kuzu')
        if cls.GRAPH_BACKEND not in _valid_backends:
            errors.append(
                f"GRAPH_BACKEND 必须是 {', '.join(_valid_backends)} 之一，当前为 '{cls.GRAPH_BACKEND}'"
            )

        # T6.4: 校验 DEERFLOW_MODEL —— 未知模型直接报错（启动期暴露拼写错误，而非 40 分钟后）；
        # 缺失对应 Key 仅告警（claude/codex 用本机订阅无需 Key；缺 Key 会在 POST /run 的 preflight 拦截）。
        _df_model = (cls.DEERFLOW_MODEL or 'claude').strip().lower()
        if _df_model not in cls.SUPPORTED_DEERFLOW_MODELS:
            errors.append(
                f"DEERFLOW_MODEL 必须是 {', '.join(cls.SUPPORTED_DEERFLOW_MODELS)} 之一，当前为 '{cls.DEERFLOW_MODEL}'"
            )
        else:
            _key_env = cls.DEERFLOW_KEY_ENV.get(_df_model)
            if _key_env and not os.environ.get(_key_env, '').strip():
                import logging
                logging.getLogger('mirofish.config').warning(
                    "DEERFLOW_MODEL=%s 需要环境变量 %s，当前未设置（研究阶段将失败）。", _df_model, _key_env
                )
        return errors


# ------------------------------------------------------------------
# DeerFlow 子进程兼容：deer-flow/config.yaml 在加载时会“贪婪地”解析所有模型 stanza 里的
# $VAR（api_key），任意一个未设置的环境变量都会让整份 config 解析直接抛错——从而连带
# 拖垮当前实际选用的研究模型（如 claude）。因此为所有提供方专属 Key 环境变量预置空默认值
# （仅在未设置时），保证未配置的提供方 stanza 也能被安全解析；真正选用某提供方时，
# apply_provider 会把真实 Key 写入对应变量。子进程通过 env=dict(os.environ) 继承这些值。
for _meta in Config.PROVIDER_META.values():
    _ke = _meta.get('key_env')
    if _ke:
        os.environ.setdefault(_ke, '')
