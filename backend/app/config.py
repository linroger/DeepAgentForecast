"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
import threading
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

    # 串行化 apply_provider 对共享 Config 类属性 + os.environ + .env 的读改写（EXECPLAN2 F-8-4），
    # 避免并发切换提供方时与正在读取配置的管线发生竞态/撕裂。
    _provider_lock = threading.Lock()

    # —— LLM 可观测性 / 缓存 / 预算（EXECPLAN2 I-5-0/I-5-2/I-6-0/I-5-3）——
    # 计量默认开（开销极小，仅累加计数）；缓存与预算默认关，保持现有行为。
    LLM_TELEMETRY_ENABLED = os.environ.get('LLM_TELEMETRY_ENABLED', 'True').strip().lower() == 'true'
    # 内容寻址缓存：对完全相同的 chat()/chat_json() 调用复用结果（同一管线内的抽取/分解去重）。
    LLM_CACHE_ENABLED = os.environ.get('LLM_CACHE_ENABLED', 'False').strip().lower() == 'true'
    # 每个 run 的 token / 成本上限（0=不限）。超限后下一次 LLM 调用抛 BudgetExceeded，止血式中止。
    LLM_RUN_BUDGET_TOKENS = int(os.environ.get('LLM_RUN_BUDGET_TOKENS', '0') or '0')
    LLM_RUN_BUDGET_USD = float(os.environ.get('LLM_RUN_BUDGET_USD', '0') or '0')

    # —— 双层模型路由（EXECPLAN2 I-6-2）——
    # 把机械型结构化调用（子查询分解 / 受访者选择 / 访谈问题生成 / JSON 修复重试 /
    # 图谱实体边抽取）路由到更便宜更快的 "fast" 模型，把质量敏感的合成型调用
    # （人设生成 / 报告规划 / 章节合成）留在旗舰 "strong" 模型。默认关：未开启时
    #  tier 参数为 no-op，所有调用一律走当前 LLM_MODEL_NAME（行为与现状逐字节一致）。
    # 配置错误（未设 fast/strong 模型）一律回退到 strong/当前模型，绝不报错。
    LLM_TIERED_ROUTING = os.environ.get('LLM_TIERED_ROUTING', 'False').strip().lower() == 'true'
    # fast / strong 模型别名（留空 → 回退到当前 LLM_MODEL_NAME，见 fast_model()/strong_model()）。
    # 仅在 LLM_TIERED_ROUTING=true 且为 OpenAI 兼容提供方时生效；CLI 订阅提供方（claude-cli/
    # codex-cli）只有单一订阅模型，tier 自动降级为 no-op（graceful degradation）。
    LLM_FAST_MODEL = (os.environ.get('LLM_FAST_MODEL') or '').strip() or None
    LLM_STRONG_MODEL = (os.environ.get('LLM_STRONG_MODEL') or '').strip() or None
    # 可选：让 fast tier 走一个完全不同的 OpenAI 兼容提供方（如本地廉价抽取 + 远端旗舰合成）。
    # 留空 → fast tier 复用当前提供方/连接参数，仅切换模型名。设置后需配合 LLM_FAST_BASE_URL /
    # LLM_FAST_API_KEY（缺任一则忽略此项、回退为「同提供方切模型」）。
    LLM_FAST_PROVIDER = (os.environ.get('LLM_FAST_PROVIDER') or '').strip().lower() or None
    LLM_FAST_BASE_URL = (os.environ.get('LLM_FAST_BASE_URL') or '').strip() or None
    LLM_FAST_API_KEY = (os.environ.get('LLM_FAST_API_KEY') or '').strip() or None

    # —— 自适应上下文预算（EXECPLAN2 I-6-4）——
    # 把散落在各处的硬编码字符切片（persona context[:3000] / 前文章节[:8000] /
    #  related_facts[:25] 等）换成「按提供方上下文窗口动态计算」的预算化截断：
    #  大窗口模型（MiniMax 512K / DeepSeek 1M）塞入更多事实与更长前文以提升 grounding，
    #  小窗口模型收紧以规避静默截断导致的 JSON 断裂。默认关：未开启时各调用点回退到
    #  原有硬编码切片（逐字节一致）。估算器为近似值（≈4 字符/token），故保留充裕的
    #  RESERVED_COMPLETION_TOKENS 安全余量。
    ADAPTIVE_CONTEXT = os.environ.get('ADAPTIVE_CONTEXT', 'False').strip().lower() == 'true'
    # 预留给「补全输出」的 token 余量（从可用窗口中扣除，避免 prompt 顶满窗口后无处生成）。
    RESERVED_COMPLETION_TOKENS = int(os.environ.get('RESERVED_COMPLETION_TOKENS', '8192') or '8192')
    # 单条上下文条目（单个事实/单段前文）允许占用的硬上限 token 数，防止某一超长条目吃光整个预算。
    CONTEXT_ITEM_MAX_TOKENS = int(os.environ.get('CONTEXT_ITEM_MAX_TOKENS', '4096') or '4096')
    # 各提供方上下文窗口（token）。未列出的提供方回退到保守默认 32K（见 context_window_for）。
    # 注意：这里按提供方粒度而非具体模型；fast/strong 同提供方时共用此窗口。
    PROVIDER_CONTEXT_WINDOWS = {
        'openai': 128000,
        'kimi': 256000,
        'minimax': 512000,
        'deepseek': 1000000,
        'qwen': 131072,
        'glm': 200000,
        # CLI 订阅提供方：claude/codex 当前主力模型均为 200K 窗口量级，给保守值。
        'claude-cli': 200000,
        'codex-cli': 200000,
    }
    DEFAULT_CONTEXT_WINDOW = int(os.environ.get('DEFAULT_CONTEXT_WINDOW', '32000') or '32000')

    @classmethod
    def fast_model(cls):  # EXECPLAN2 I-6-2
        """fast tier 模型名：LLM_FAST_MODEL 优先，未设则回退到当前 LLM_MODEL_NAME（不报错）。"""
        return cls.LLM_FAST_MODEL or cls.LLM_MODEL_NAME

    @classmethod
    def strong_model(cls):  # EXECPLAN2 I-6-2
        """strong tier 模型名：LLM_STRONG_MODEL 优先，未设则回退到当前 LLM_MODEL_NAME（不报错）。"""
        return cls.LLM_STRONG_MODEL or cls.LLM_MODEL_NAME

    @classmethod
    def context_window_for(cls, provider):  # EXECPLAN2 I-6-4
        """返回某提供方的上下文窗口（token）；未知提供方回退到 DEFAULT_CONTEXT_WINDOW。"""
        return int(cls.PROVIDER_CONTEXT_WINDOWS.get((provider or '').lower(), cls.DEFAULT_CONTEXT_WINDOW))

    # 报告完成后追加一遍「结构化预测」抽取：机器可读的情景+概率+判定标准+引用审计
    # （EXECPLAN2 I-3-0/I-9-1/I-3-1）。默认关，保持现有纯文本报告行为。落 forecast.json。
    REPORT_STRUCTURED_FORECAST = os.environ.get('REPORT_STRUCTURED_FORECAST', 'False').strip().lower() == 'true'
    # 结构化预测后追加红队自校准（纠正过度自信/基率忽视，EXECPLAN2 I-3-5）。默认关（多一次 LLM 调用）。
    REPORT_FORECAST_SELF_CRITIQUE = os.environ.get('REPORT_FORECAST_SELF_CRITIQUE', 'False').strip().lower() == 'true'
    # OASIS 抽样/人设生成确定性种子（EXECPLAN2 I-7-2；0/空=随机，复现/集成跑设同一正整数）。
    SIM_SEED = int(os.environ.get('SIM_SEED', '0') or '0')

    # —— EXECPLAN2 第二波改进旋钮（单一真源；各消费方此前经 getattr 读取，这里收口 + 文档化）——
    GRAPH_SEARCH_RECIPE = os.environ.get('GRAPH_SEARCH_RECIPE', 'rrf').strip().lower()          # I-1-0/I-1-6 检索 recipe
    RESEARCH_QUALITY_GATE = os.environ.get('RESEARCH_QUALITY_GATE', 'False').strip().lower() == 'true'  # I-0-3 研究后质量门
    PIPELINE_STRICT_SCHEMA = os.environ.get('PIPELINE_STRICT_SCHEMA', 'True').strip().lower() == 'true'  # I-4-4 状态模式版本校验
    SIM_EMERGENT_METRICS = os.environ.get('SIM_EMERGENT_METRICS', 'False').strip().lower() == 'true'     # I-2-0 涌现结构指标
    IPC_TELEMETRY_ENABLED = os.environ.get('IPC_TELEMETRY_ENABLED', 'False').strip().lower() == 'true'   # I-5-5 IPC 延迟计量
    ONTOLOGY_TEMPLATE = os.environ.get('ONTOLOGY_TEMPLATE', 'social_opinion').strip().lower()  # I-1-3 领域自适应本体模板
    PERSONA_EGO_RETRIEVAL = os.environ.get('PERSONA_EGO_RETRIEVAL', 'False').strip().lower() == 'true'   # I-1-5 自我中心人设上下文
    API_V1_ENABLED = os.environ.get('API_V1_ENABLED', 'False').strip().lower() == 'true'       # I-9-5 稳定版程序化 API /api/v1
    MODEL_COMPARISON_ENABLED = os.environ.get('MODEL_COMPARISON_ENABLED', 'False').strip().lower() == 'true'  # I-9-4 模型对比
    REPORT_TELEMETRY = os.environ.get('REPORT_TELEMETRY', 'True').strip().lower() == 'true'     # I-5-4 报告级 LLM 计量汇总
    REPORT_SIGNAL_PACK = os.environ.get('REPORT_SIGNAL_PACK', 'False').strip().lower() == 'true'  # I-3-2 每章注入定量信号包
    REPORT_COMPARISON_TABLE = os.environ.get('REPORT_COMPARISON_TABLE', 'False').strip().lower() == 'true'  # I-3-4 基线-情景对比表
    RECORD_RUN_MANIFEST = os.environ.get('RECORD_RUN_MANIFEST', 'True').strip().lower() == 'true'  # I-8-1 复现清单 run.json

    # —— EXECPLAN2 第三波改进旋钮（剩余 L-effort 新能力；全部默认关，留空即保持当前行为）——
    # 预测质量回归评测开关（EXECPLAN2 I-7-7）：opt-in，绝不进默认 CI。开启后 eval_forecast_quality.py
    # 用 LLM-judge 按 rubric 给固定情景集打分并与 baseline 对比。默认关。
    EVAL_ENABLED = os.environ.get('EVAL_ENABLED', 'False').strip().lower() == 'true'  # I-7-7 预测质量评测

    # LLM提供方（默认使用 Claude Code CLI 订阅）
    # claude-cli: 通过本机 `claude` CLI 调用（使用 Claude Code 订阅，无需 API Key）
    # codex-cli:  通过本机 `codex` CLI 调用（使用 Codex 订阅，无需 API Key）
    # openai:     回退到 OpenAI 兼容 API（需要 LLM_API_KEY）
    # kimi:       Kimi-for-coding（api.kimi.com/coding，OpenAI 兼容 + coding-agent UA 网关）
    # minimax:    MiniMax 代码计划（api.minimaxi.com 国内版，OpenAI 兼容，MiniMax-M3 推理模型）
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'claude-cli').strip().lower()

    # Kimi-for-coding 默认连接参数（provider=kimi 且未显式覆盖时启用）
    _KIMI_DEFAULT_BASE_URL = 'https://api.kimi.com/coding/v1'
    # K2.7 Code：网关同时接受 'kimi-k2.7' 与历史别名 'kimi-for-coding'（/models 仅列后者，
    # 但补全请求 echo 回 'kimi-k2.7'）。注意该模型对 temperature 有硬约束（开推理=1/关=0.6），
    # 由 LLMClient._coerce_temperature 统一兜底。
    _KIMI_DEFAULT_MODEL = 'kimi-k2.7'
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

        # 在锁内完成「改类属性 + 改 os.environ + 写 .env」整段读改写（F-8-4）。
        with cls._provider_lock:
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

    # —— OASIS 并发上限（每轮在飞 LLM 请求数）单一真源（EXECPLAN2 I-8-4）——
    # 此前这两个旋钮只在 utils/oasis_llm.py::get_oasis_semaphore 里经 os.environ 直读，
    # 绕过了集中式 Config 配置面——对 doctor/validate/run 清单不可见、无法记录复现。
    # 提升为一等 Config 属性，默认值与 oasis_llm.py 的 DEFAULT_*_SEMAPHORE 逐字节一致
    # （CLI 提供方 8、OpenAI 兼容提供方 30），故 env 未设时行为字节稳定不变。
    # CLI 提供方(claude-cli/codex-cli)：每个调用 spawn 子进程，8 是吞吐与负载的稳妥平衡。
    OASIS_CLI_SEMAPHORE = int(os.environ.get('OASIS_CLI_SEMAPHORE', '8') or '8')
    # OpenAI 兼容提供方：纯 HTTP 并发，30 给足吞吐。
    OASIS_SEMAPHORE = int(os.environ.get('OASIS_SEMAPHORE', '30') or '30')
    
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
    # 深度研究 per-KIQ / per-actor 子代理扇出（EXECPLAN2 I-0-4）：开场 scope pass 产出种子清单后，
    # 并行派发若干 scoped 子调查，合并工作笔记再做矛盾核验+综合。默认关 = 线性协议不变。
    # 经 env 下发给 deerflow 子进程（独立 venv）读取。
    RESEARCH_DEEP_FANOUT = os.environ.get('RESEARCH_DEEP_FANOUT', 'false').strip().lower() == 'true'
    # 扇出宽度上限（并行子调查数）；防止子代理把工具/LLM 预算放大失控。
    RESEARCH_FANOUT_WIDTH = int(os.environ.get('RESEARCH_FANOUT_WIDTH', '4') or '4')

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
    # 把已发现的社区(派系)做成一等可检索结构：报告侧 faction_brief 工具 + 人设侧群体身份
    # （EXECPLAN2 I-1-2）。默认跟随 GRAPH_BUILD_COMMUNITIES（无社区节点时 faction_brief 自动
    # 降级到 coalition_map 的行为日志聚类）。显式设 true/false 可覆盖。
    GRAPH_COMMUNITY_RETRIEVAL = (
        os.environ.get('GRAPH_COMMUNITY_RETRIEVAL', '').strip().lower() == 'true'
        if os.environ.get('GRAPH_COMMUNITY_RETRIEVAL', '').strip() != ''
        else GRAPH_BUILD_COMMUNITIES
    )
    # 建图末尾跑一遍 LLM 实体消解 / 规范别名合并（EXECPLAN2 I-1-4）：把 'OpenAI'/'OpenAI 公司'/
    # '@OpenAI' 等同实体的分裂节点合并到 actors.json 的规范名上。默认关（过度合并风险高）。
    GRAPH_RESOLVE_ENTITIES = os.environ.get('GRAPH_RESOLVE_ENTITIES', 'false').strip().lower() == 'true'
    # 合并所需的最小 embedding 余弦相似度（规范名匹配 + 此阈值 双重门，降低误合并）。
    GRAPH_RESOLVE_SIM_THRESHOLD = float(os.environ.get('GRAPH_RESOLVE_SIM_THRESHOLD', '0.88') or '0.88')
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
    # 逐智能体动态情感状态（情绪/精力/立场强度/疲劳）按轮更新并注入该轮提示（EXECPLAN2 I-2-1）。
    # 默认关 = 每轮静态人设（与现状逐字节一致）。
    SIM_AGENT_DYNAMICS = os.environ.get('SIM_AGENT_DYNAMICS', 'false').strip().lower() == 'true'
    # 情感状态更新的学习率/速率常数（有界 clamp，保守取值；仅 SIM_AGENT_DYNAMICS=true 时生效）。
    SIM_DYNAMICS_MOOD_LR = float(os.environ.get('SIM_DYNAMICS_MOOD_LR', '0.25') or '0.25')
    SIM_DYNAMICS_OPINION_LR = float(os.environ.get('SIM_DYNAMICS_OPINION_LR', '0.15') or '0.15')
    SIM_DYNAMICS_FATIGUE_RATE = float(os.environ.get('SIM_DYNAMICS_FATIGUE_RATE', '0.20') or '0.20')
    SIM_DYNAMICS_FATIGUE_DECAY = float(os.environ.get('SIM_DYNAMICS_FATIGUE_DECAY', '0.10') or '0.10')
    # 模拟中断后从上次完成的轮次继续（而非从第 0 轮重启），依赖 OASIS DB 持久性（EXECPLAN2 I-4-2）。
    # 默认关 = 全量重启（与现状一致）。
    SIM_RESUME_FROM_ROUND = os.environ.get('SIM_RESUME_FROM_ROUND', 'false').strip().lower() == 'true'

    # --- 报告（Phase 4）---
    # 每节最少/对话模式最多工具调用（与 REPORT_AGENT_MAX_TOOL_CALLS 配套；T4.4 接入硬编码值）
    REPORT_AGENT_MIN_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MIN_TOOL_CALLS', '4'))
    REPORT_AGENT_MAX_TOOL_CALLS_CHAT = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS_CHAT', '2'))
    # 用 DeerFlow ClaudeChatModel 的原生 tool calling 取代手搓 ReAct（仅 claude；默认关，最后启用）(T4.5)
    REPORT_NATIVE_TOOLS = os.environ.get('REPORT_NATIVE_TOOLS', 'false').strip().lower() == 'true'
    # 并发生成报告章节（EXECPLAN2 I-6-3）：>1 时正文章节走线程池并行，摘要/结论章节最后串行
    # （依赖正文全文）。默认 1 = 严格串行（与现状逐字节一致）。章节级 LLM 并发受 OASIS 信号量同源约束。
    REPORT_SECTION_CONCURRENCY = int(os.environ.get('REPORT_SECTION_CONCURRENCY', '1') or '1')
    # 章节上下文模式（I-6-3）：full = 每章注入此前所有章节全文（现状）；brief = 注入大纲+各章
    # 1-2 句摘要（去除 O(N²) 上下文膨胀）。并发模式下正文章节强制用 brief（并行时拿不到彼此全文）。
    REPORT_SECTION_CONTEXT_MODE = os.environ.get('REPORT_SECTION_CONTEXT_MODE', 'full').strip().lower()

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

        # EXECPLAN2 I-8-4: OASIS 并发上限纳入集中式校验。<1 会让 get_oasis_semaphore
        # 返回 0/负值（信号量直接死锁），故启动期硬性拦截，而非运行时悬挂。
        for _sem_name in ('OASIS_CLI_SEMAPHORE', 'OASIS_SEMAPHORE'):
            _sem_val = getattr(cls, _sem_name, None)
            if not isinstance(_sem_val, int) or _sem_val < 1:
                errors.append(f"{_sem_name} 必须是 >=1 的整数，当前为 '{_sem_val}'")
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
