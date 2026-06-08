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
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
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

    # LLM配置（provider=openai/kimi/minimax 时统一使用 OpenAI 格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL') or (
        _KIMI_DEFAULT_BASE_URL if _is_kimi
        else _MINIMAX_DEFAULT_BASE_URL if _is_minimax
        else 'https://api.openai.com/v1'
    )
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME') or (
        _KIMI_DEFAULT_MODEL if _is_kimi
        else _MINIMAX_DEFAULT_MODEL if _is_minimax
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

    @classmethod
    def reasoning_extra_body(cls):
        """OpenAI 兼容推理提供方(kimi/minimax)关闭推理用的 extra_body；其它提供方返回 None。

        kimi 与 MiniMax-M3 都接受 ``extra_body={"thinking": {"type": "disabled"}}``。
        """
        if cls.LLM_PROVIDER == 'kimi' and cls.LLM_KIMI_DISABLE_THINKING:
            return {"thinking": {"type": "disabled"}}
        if cls.LLM_PROVIDER == 'minimax' and cls.LLM_MINIMAX_DISABLE_THINKING:
            return {"thinking": {"type": "disabled"}}
        return None

    # 向后兼容别名（旧调用点）：等价于 reasoning_extra_body。
    @classmethod
    def kimi_extra_body(cls):
        return cls.reasoning_extra_body()

    # —— 运行时模型提供方切换（供 /api/settings 使用）——
    # 每个提供方的展示元数据：是否需要 API Key、对应的 DeerFlow 研究模型（deer-flow/config.yaml
    # 当前仅定义了 claude + minimax 两个研究模型，故其余提供方的深度研究回退到 claude/OAuth）。
    PROVIDER_META = {
        'claude-cli': {'label': 'Claude Code（CLI 订阅）', 'needs_key': False, 'deerflow_model': 'claude'},
        'codex-cli':  {'label': 'Codex（CLI 订阅）',       'needs_key': False, 'deerflow_model': 'claude'},
        'openai':     {'label': 'OpenAI 兼容 API',          'needs_key': True,  'deerflow_model': 'claude',
                       'default_base': 'https://api.openai.com/v1', 'default_model': 'gpt-4o-mini'},
        'kimi':       {'label': 'Kimi-for-coding',          'needs_key': True,  'deerflow_model': 'claude',
                       'default_base': _KIMI_DEFAULT_BASE_URL, 'default_model': _KIMI_DEFAULT_MODEL},
        'minimax':    {'label': 'MiniMax 代码计划（国内版）', 'needs_key': True,  'deerflow_model': 'minimax',
                       'default_base': _MINIMAX_DEFAULT_BASE_URL, 'default_model': _MINIMAX_DEFAULT_MODEL},
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
        is_openai_compat = provider in ('openai', 'kimi', 'minimax')

        keeps_existing_key = (provider == cls.LLM_PROVIDER and bool(cls.LLM_API_KEY))
        if meta.get('needs_key') and not ((api_key or '').strip() or keeps_existing_key):
            raise ValueError(f"提供方 {provider} 需要 API Key")

        cls.LLM_PROVIDER = provider
        cls._is_kimi = provider == 'kimi'
        cls._is_minimax = provider == 'minimax'
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
                if provider == 'minimax':
                    env_updates['MINIMAX_API_KEY'] = cls.LLM_API_KEY

        for k, v in env_updates.items():
            os.environ[k] = v
        cls._persist_env(env_updates)
        return cls.provider_info()

    @classmethod
    def _persist_env(cls, updates):
        """把 key=value 安全 upsert 进项目根 .env（best-effort，失败不抛）。"""
        try:
            env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env'))
            lines = []
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    lines = f.read().splitlines()
            remaining = dict(updates)
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
            tmp = env_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write('\n'.join(out) + '\n')
            os.replace(tmp, env_path)
        except Exception:
            pass

    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
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
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    # 支持的 LLM 提供方
    SUPPORTED_LLM_PROVIDERS = ('claude-cli', 'codex-cli', 'openai', 'kimi', 'minimax')

    # ============================================================
    # DeerFlow 深度研究集成（前置 Step 0：用一个 prompt 自动调研生成种子材料）
    # DeerFlow runs in its OWN venv (separate dependency tree). MiroFish launches
    # backend/scripts or the sibling deer-flow repo's deerflow_research.py via
    # subprocess and consumes the file-based handoff contract.
    # ============================================================
    # 默认指向同级目录的 deer-flow 仓库： <MiroFish-0.1.2>/../deer-flow
    DEERFLOW_DIR = os.environ.get(
        'DEERFLOW_DIR',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..', 'deer-flow'))
    )
    # DeerFlow venv 的 python（留空则自动探测 .venv，再退回到 `uv run`）
    DEERFLOW_PYTHON = os.environ.get('DEERFLOW_PYTHON', '').strip() or None
    # DeerFlow config.yaml 中的模型名（默认 claude → Claude Code 订阅 OAuth）
    DEERFLOW_MODEL = os.environ.get('DEERFLOW_MODEL', 'claude').strip()
    # 研究深度：quick / standard / deep
    DEERFLOW_RESEARCH_DEPTH = os.environ.get('DEERFLOW_RESEARCH_DEPTH', 'standard').strip().lower()
    # 研究报告/结构化输出语言（MiroFish 面向中文舆论，默认中文；留空交给模型自选）
    DEERFLOW_RESEARCH_LANGUAGE = os.environ.get('DEERFLOW_RESEARCH_LANGUAGE', 'Chinese').strip() or None
    # 研究阶段最长等待秒数
    DEERFLOW_RESEARCH_TIMEOUT = int(os.environ.get('DEERFLOW_RESEARCH_TIMEOUT', '2400'))
    # 是否启用 DeerFlow 子代理（并行 scoped workers，更深但更慢）
    DEERFLOW_SUBAGENTS = os.environ.get('DEERFLOW_SUBAGENTS', 'false').strip().lower() == 'true'
    # 统一管线产物目录
    PIPELINE_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/pipelines')

    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []

        if cls.LLM_PROVIDER not in cls.SUPPORTED_LLM_PROVIDERS:
            errors.append(
                f"LLM_PROVIDER 必须是 {', '.join(cls.SUPPORTED_LLM_PROVIDERS)} 之一，"
                f"当前为 '{cls.LLM_PROVIDER}'"
            )

        # openai / kimi / minimax 提供方需要 API Key；CLI 提供方使用本机订阅
        if cls.LLM_PROVIDER in ('openai', 'kimi', 'minimax') and not cls.LLM_API_KEY:
            errors.append(f"LLM_PROVIDER={cls.LLM_PROVIDER} 时必须配置 LLM_API_KEY")

        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        return errors

