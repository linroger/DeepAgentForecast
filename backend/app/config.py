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
    # SIM-9 / CACHE-1：默认开——进程内、精确键、有界 LRU；仅确定性 attempt-0 调用命中，
    # 故多样性不受影响。跨 seed/resume/fork 的 prepare/persona/config-gen 复用收益最大。
    LLM_CACHE_ENABLED = os.environ.get('LLM_CACHE_ENABLED', 'True').strip().lower() == 'true'
    # 每个 run 的 token / 成本上限（0=不限）。超限后下一次 LLM 调用抛 BudgetExceeded，止血式中止。
    LLM_RUN_BUDGET_TOKENS = int(os.environ.get('LLM_RUN_BUDGET_TOKENS', '0') or '0')
    LLM_RUN_BUDGET_USD = float(os.environ.get('LLM_RUN_BUDGET_USD', '0') or '0')
    # ITEM-18：每个 provider 的 $/Mtok 成本覆盖表（JSON），形如 {"openai":[5.0,15.0],"myprov":[0.5,1.5]}
    # （每百万 token 的 [输入, 输出] 美元价）。叠加在 telemetry._COST_PER_1K 的保守内建默认之上（同名覆盖、
    # 新名新增）。留空=纯用内建默认；解析失败=退回无覆盖（degrade-safe，见 telemetry._cost_overrides）。
    LLM_COST_PER_MTOK = os.environ.get('LLM_COST_PER_MTOK', '').strip()

    # —— 调优后的 LLM HTTP 客户端（R2-EXEC-6）——
    # 默认 httpx 把 keepalive 连接上限压在 20 且无多路复用，并发抬高后每次调用都要重做 TLS 握手，
    # 把 R2-EXEC-1 的并发旋钮卡在「连不上代理」。LLM_HTTP2=true 启用 HTTP/2 多路复用 + 更大 keepalive；
    # 协议协商失败时回退 http1（degrade-safe）。LLM_HTTP_KEEPALIVE 抬高最大 keepalive 连接数（原 20）。
    LLM_HTTP2 = os.environ.get('LLM_HTTP2', 'True').strip().lower() == 'true'
    LLM_HTTP_KEEPALIVE = int(os.environ.get('LLM_HTTP_KEEPALIVE', '128') or '128')

    # —— 双层模型路由（EXECPLAN2 I-6-2）——
    # 把机械型结构化调用（子查询分解 / 受访者选择 / 访谈问题生成 / JSON 修复重试 /
    # 图谱实体边抽取）路由到更便宜更快的 "fast" 模型，把质量敏感的合成型调用
    # （人设生成 / 报告规划 / 章节合成）留在旗舰 "strong" 模型。默认关：未开启时
    #  tier 参数为 no-op，所有调用一律走当前 LLM_MODEL_NAME（行为与现状逐字节一致）。
    # 配置错误（未设 fast/strong 模型）一律回退到 strong/当前模型，绝不报错。
    # GAP-1：默认开——把机械抽取/分解路由到 fast 模型是单调最大的 per-call 延迟杠杆。
    # 但「未设 LLM_FAST_MODEL → fast_model() 回退到 LLM_MODEL_NAME」，且 CLI 订阅提供方只有单一
    # 模型 → tier 自动降级为 no-op；因此本旗标在没有真正 fast 模型前是 inert 的（degrade-safe），
    # 真正提速需在 .env 设 LLM_FAST_MODEL=<proxy 上的非推理快模型>。
    LLM_TIERED_ROUTING = os.environ.get('LLM_TIERED_ROUTING', 'True').strip().lower() == 'true'
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

    # —— 自适应上下文预算（EXECPLAN2 I-6-4；RQ-7 已接线）——
    # RQ-7 状态：**已接线**——token_budget.py（estimate_tokens/truncate_to_tokens/context_budget/
    # slice_budget_chars/clamp_chars/fit_to_budget）现由三处生产调用点消费：
    #  (1) report_agent._prior_section_char_budget —— 前序章节 [:8000] 切片按提供方窗口预算化；
    #  (2) oasis_profile_generator —— 人设上下文 [:3000] 切片按窗口放宽（floor=PERSONA_CONTEXT_FLOOR_CHARS）；
    #  (3) simulation_config_generator —— 世界简报上限 1400→SIM_WORLD_BRIEF_MAX_CHARS（大窗口）。
    # 设计：把散落各处的硬编码字符切片换成「按提供方上下文窗口动态计算」的预算化截断——
    #  大窗口模型（MiniMax 512K / DeepSeek 1M）塞入更多事实与更长前文以提升 grounding，
    #  小窗口模型守住今天的固定切片作为 floor（绝不收紧）。估算器为近似值（≈4 字符/token），
    #  故保留充裕的 RESERVED_COMPLETION_TOKENS 安全余量。
    # 默认开（true）：接线后打开即让长研究报告真正抵达下游提示词（大窗口提供方）；
    #  任何 token_budget 异常/小窗口/未知提供方一律退回固定切片（degrade-safe），故默认开是安全的。
    #  设 false 可逐字节复现接线前的固定切片行为。
    ADAPTIVE_CONTEXT = os.environ.get('ADAPTIVE_CONTEXT', 'True').strip().lower() == 'true'
    # 预留给「补全输出」的 token 余量（从可用窗口中扣除，避免 prompt 顶满窗口后无处生成）。
    RESERVED_COMPLETION_TOKENS = int(os.environ.get('RESERVED_COMPLETION_TOKENS', '8192') or '8192')
    # 单条上下文条目（单个事实/单段前文）允许占用的硬上限 token 数，防止某一超长条目吃光整个预算。
    CONTEXT_ITEM_MAX_TOKENS = int(os.environ.get('CONTEXT_ITEM_MAX_TOKENS', '4096') or '4096')
    # RQ-7：人设上下文切片的 floor（字符）。ADAPTIVE_CONTEXT=false 或小窗口时即用此值（=接线前的
    # context[:3000]，行为不变）；ADAPTIVE_CONTEXT=true 且大窗口时以此为下限向上放宽。
    PERSONA_CONTEXT_FLOOR_CHARS = int(os.environ.get('PERSONA_CONTEXT_FLOOR_CHARS', '3000') or '3000')
    # RQ-7：模拟世界简报的字符上限（ceiling）。ADAPTIVE_CONTEXT=true 且大窗口时把 ≤1400（floor）
    # 抬到此值以承载更完整的世界背景；ADAPTIVE_CONTEXT=false 或小窗口 → 守住 1400 floor（行为不变）。
    SIM_WORLD_BRIEF_MAX_CHARS = int(os.environ.get('SIM_WORLD_BRIEF_MAX_CHARS', '3000') or '3000')
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

    # 结构化预测（机器可读的情景+概率+判定标准+引用审计，EXECPLAN2 I-3-0/I-9-1/I-3-1；
    # NEXTSTEPS P0-1）。默认开：预测是本产品的交付物，结构化骨架应是一等公民而非旁支。
    # 关闭则回到旧的纯文本报告行为（degrade-safe）。落 forecast.json。
    REPORT_STRUCTURED_FORECAST = os.environ.get('REPORT_STRUCTURED_FORECAST', 'True').strip().lower() == 'true'
    # QUALITY-OPT S1: before declaring a run completed, validate the real deliverable —
    # HARD-FAIL (status=failed) on an all-placeholder report or missing/empty forecast.json,
    # and record a degraded health block (consumed by status API + sim-caveat) for hollow/
    # truncated sims. Stops the "fake completed" runs (8/13 of the audited corpus).
    PIPELINE_HEALTH_GATE = os.environ.get('PIPELINE_HEALTH_GATE', 'True').strip().lower() == 'true'
    # NEXTSTEPS P0-1：在撰写任何章节叙事**之前**先从信号包+forecast_inputs 推导「预测骨架」
    # （情景+概率+判定标准），强制 MECE 纪律并把骨架注入每章提示词，让叙事对齐可证伪目标。
    # 默认开；关闭则回退为旧的「报告写完后再从成稿抽取」行为（degrade-safe）。
    REPORT_FORECAST_SPINE_FIRST = os.environ.get('REPORT_FORECAST_SPINE_FIRST', 'True').strip().lower() == 'true'
    # 结构化预测后追加红队自校准（纠正过度自信/基率忽视，EXECPLAN2 I-3-5；NEXTSTEPS P2-1）。
    # 默认开：与基率锚定+inter-seed 一致度配合做 anchor-and-adjust，纠正内视过度自信（多一次 LLM 调用）。
    REPORT_FORECAST_SELF_CRITIQUE = os.environ.get('REPORT_FORECAST_SELF_CRITIQUE', 'True').strip().lower() == 'true'
    # NEXTSTEPS P2-3：发布门。结构化预测推导后，对「喂给情景的定量声明」要求引用覆盖率≥阈值，
    # 否则把整体 confidence 降级为 low；并强制存在一个「维持现状/兜底」情景、拒绝无依据的退化熵。
    # 把品控写进 forecast.json.quality。默认开；关闭=不设门（degrade-safe）。
    REPORT_PUBLISH_GATE = os.environ.get('REPORT_PUBLISH_GATE', 'True').strip().lower() == 'true'
    REPORT_PUBLISH_GATE_MIN_COVERAGE = float(os.environ.get('REPORT_PUBLISH_GATE_MIN_COVERAGE', '0.5') or '0.5')
    # NEXTSTEPS P2-2：在报告末尾追加一个**确定性**的「如何验证本预测」章节——逐情景列可证伪判定
    # 标准 + 来自 forecast_inputs 的带日期/触发观察指标，并把指标-情景映射写进 forecast.json 供
    # 解析调度器使用。默认开；无结构化预测/无情景时自动跳过（degrade-safe）。
    REPORT_RESOLUTION_SECTION = os.environ.get('REPORT_RESOLUTION_SECTION', 'True').strip().lower() == 'true'
    # VIZ-1：确定性报告可视化器（无 LLM）。把已落盘的结构化工件（forecast/timeline/actors/
    # world_state_trajectory/comparison/校准账本）渲染成 Mermaid 代码块 + matplotlib PNG，落盘
    # reports/{id}/charts/ 与 viz_manifest.json。主开关默认开；关闭=完全不生成图（degrade-safe）。
    REPORT_VISUALIZER = os.environ.get('REPORT_VISUALIZER', 'True').strip().lower() == 'true'
    # VIZ-1：Mermaid 族（零依赖）开关——时间线/因果路径/派系/角色关系网络。默认开。
    REPORT_VIZ_MERMAID = os.environ.get('REPORT_VIZ_MERMAID', 'True').strip().lower() == 'true'
    # VIZ-1：matplotlib PNG 族开关——情景误差棒/模型 vs 市场哑铃/世界态堆叠面积/对比分组柱/校准
    # 曲线。默认开；matplotlib 未安装时该族自动无效（仅 Mermaid），无需改此旋钮。
    REPORT_VIZ_CHARTS = os.environ.get('REPORT_VIZ_CHARTS', 'True').strip().lower() == 'true'
    # VIZ-1：PNG 渲染 dpi（PDF 宽度下可读性；下限 72）。
    REPORT_VIZ_DPI = int(os.environ.get('REPORT_VIZ_DPI', '160') or '160')
    # VIZ-1：网络图/因果图/派系图的节点上限（防止巨图不可读；超出即截断，确定性保留首现节点）。
    REPORT_VIZ_MAX_NODES = int(os.environ.get('REPORT_VIZ_MAX_NODES', '40') or '40')
    # VIZ-1（ITEM-16）：交互式 HTML 图表族开关——用 plotly 生成 matplotlib PNG 的可交互等价物
    # （情景误差棒/模型vs市场哑铃/市场价格历史折线/世界态堆叠面积），落 reports/{id}/charts/*.html
    # （plotly.js 内联，完全离线自包含），manifest type='html'。默认开；plotly 未安装时该族自动
    # 无效（与 PNG 族并存，互不影响）。关闭=不生成 HTML 图（degrade-safe）。
    REPORT_VIZ_INTERACTIVE = os.environ.get('REPORT_VIZ_INTERACTIVE', 'True').strip().lower() == 'true'
    # VIZ-1 钩子：把 ReportVisualizer 产出的图表/图注注入成稿 full_report.md——Mermaid 块按章节
    # 标题关键词模糊匹配就地插入，未匹配的图与全部 PNG 归入文末「Visual Annex / 可视化附录」（双语
    # 随报告语言）。与 REPORT_VISUALIZER 正交：后者管「是否生成图 + 落 charts/」，此旋钮管「是否
    # 注入正文」。默认开；关闭或注入失败=成稿不含图区（图仍落盘，degrade-safe）。
    REPORT_VISUALIZATIONS = os.environ.get('REPORT_VISUALIZATIONS', 'True').strip().lower() == 'true'
    # PDF-1：GET /api/report/{id}/pdf 惰性把 full_report.md 导出为 full_report.pdf——相对图表路径
    # 绝对化 +（PATH 有 mmdc 时）预渲染 Mermaid 为 PNG，pandoc+xelatex（CJKmainfont=PingFang SC、
    # geometry margin 2.5cm、--toc），失败回退 markdown→HTML→PyMuPDF Story。按成稿 mtime 缓存。
    # 默认开；关闭=端点 404（degrade-safe）。
    REPORT_PDF_EXPORT = os.environ.get('REPORT_PDF_EXPORT', 'True').strip().lower() == 'true'
    # PDF-1：单次 pandoc 构建超时秒数（防挂死；超时即回退 PyMuPDF）。
    REPORT_PDF_TIMEOUT = int(os.environ.get('REPORT_PDF_TIMEOUT', '180') or '180')
    # NEXTSTEPS P2-4：把每份 forecast.json 追加进校准账本（horizon/resolution date 为键），已解析
    # 预测的历史 Brier/ECE surfacing 进新预测 confidence_rationale——让信心由 track record 赚得而非
    # 自评。默认开（仅 jsonl 追加/读取，无 LLM）；初期无已解析样本时对信心无影响（degrade-safe）。
    REPORT_FORECAST_LEDGER = os.environ.get('REPORT_FORECAST_LEDGER', 'True').strip().lower() == 'true'
    FORECAST_LEDGER_DIR = os.environ.get('FORECAST_LEDGER_DIR', '').strip()  # 空=PIPELINE_DATA_DIR/_forecast_ledger
    # NEXTSTEPS P3-8：把已实现关系按价投影一个「到预测时点的轨迹」（allied→likely_persists /
    # adversarial→persists_or_escalates / transactional→contingent），喂进报告信号包帮助情景分叉
    # 分析（contingent 纽带=支点）。**模型先验非证据**，块内显式标注。默认关（保守，避免被当成证据）。
    REPORT_PROJECTED_EDGES = os.environ.get('REPORT_PROJECTED_EDGES', 'False').strip().lower() == 'true'
    # OASIS 抽样/人设生成确定性种子（EXECPLAN2 I-7-2；0/空=随机，复现/集成跑设同一正整数）。
    SIM_SEED = int(os.environ.get('SIM_SEED', '0') or '0')
    # NEXTSTEPS P0-3：同问多种子集成。LLM 驱动的模拟是随机生成器，单次=单抽样；对同一图谱用
    # 不同 SIM_SEED 跑 N 次 sim+report，聚合各自 forecast.json→ensemble_forecast.json，把点估计
    # 变成带区间的分布，inter-seed 一致度→报告信心。CONF-1：默认 1→3——单抽样过度自信是最大的
    # 校准短板；额外种子只重跑 sim+report（不重跑研究/图谱），且严格串行（见 _maybe_run_seed_ensemble），
    # 故 wall-clock 只放大模拟+报告段（约 ×3），非整条管线。设 1 复现旧行为。
    N_FORECAST_SEEDS = max(1, int(os.environ.get('N_FORECAST_SEEDS', '3') or '3'))
    # PAR-3：多种子集成的并行度。此前额外种子严格串行（wall-clock ≈ ×N_FORECAST_SEEDS 的
    # sim+report 段）；每个种子跑在独立 simulation_id → 独立目录/DB/文件式 IPC/仅注入子进程的
    # env（见 _run_one_seed 的线程安全论证），故可安全并行。默认 2、上限 3（并行度越高对
    # LLM provider 的瞬时压力越大——每个种子子进程内部还各自受 OASIS 信号量约束）。设 1 =
    # 复现旧的严格串行行为（degrade-safe）。注意：共享的 graph_id 会被并发种子的图谱反馈同时
    # 写入——串行版本本就共享同一张图，Zep 服务端处理并发写；每种子的 updater 按 sim_id 独立键。
    ENSEMBLE_SEED_CONCURRENCY = max(1, min(3, int(os.environ.get('ENSEMBLE_SEED_CONCURRENCY', '2') or '2')))

    # —— 二元预测契约（QUALITY-OPT A1/A4：briefs 常要求「>=N 个二元 yes/no 预测，各含客观判定」）——
    # 研究阶段往往已产出合规的二元预测（如 research_report 的 F1-Fn），但下游 finalizer 之前只输出
    # 情景而丢弃它们。开启后：从研究报告抽取/补足 >=BINARY_FORECASTS_MIN_COUNT 条独立二元预测，
    # 各带 statement(一句)/probability(独立, 不归一)/客观 resolution_criteria(指标+阈值+日期+来源)，
    # 写入 forecast['binary_forecasts']，与 scenarios 并存。degrade-safe：抽取失败则维持旧行为。
    FORECAST_EMIT_BINARY = os.environ.get('FORECAST_EMIT_BINARY', 'True').strip().lower() == 'true'
    BINARY_FORECASTS_MIN_COUNT = max(1, int(os.environ.get('BINARY_FORECASTS_MIN_COUNT', '10') or '10'))
    # 对二元预测强制「锐利客观判定」（指标 AND 数字 AND 日期）；不达标的逐条重生成一次。
    FORECAST_BINARY_REQUIRE_SHARP = os.environ.get('FORECAST_BINARY_REQUIRE_SHARP', 'True').strip().lower() == 'true'

    # —— 校准基石（R2-CAL）——
    # R2-CAL-4：发布前给每个保留情景一个概率下限再做最终重归一，绝不发布 0%。消除「已实现但未预测」
    # 结果坐在 ~0 处的灾难性 log-loss/Brier 失败。trivial 且 tail-dominant。
    FORECAST_PROB_FLOOR = float(os.environ.get('FORECAST_PROB_FLOOR', '0.03') or '0.03')
    # R2-CAL-1 / R2-CAL-17：把预测脊柱推导 K 次（共享情景名、变 temp），汇成均值概率 + spread→confidence。
    # 全管线最便宜的「分布」：K 次廉价 LLM 调用、不重跑图谱/模拟，去掉单抽样过度自信，N_FORECAST_SEEDS=1
    # 也能给出区间。1 = 复现今天的行为（degrade-safe）。
    REPORT_SPINE_SELFCONSISTENCY_K = max(1, int(os.environ.get('REPORT_SPINE_SELFCONSISTENCY_K', '5') or '5'))
    # R2-CAL-2：集成聚合用 log-odds（几何）pooling 的 extremizing 因子；算术均值作为诊断保留。
    # 算术平均欠自信（拉向 0.5）；extremized log-odds pooling 跨 seed/自一致抽样锐化校准。
    ENSEMBLE_EXTREMIZE_A = float(os.environ.get('ENSEMBLE_EXTREMIZE_A', '2.0') or '2.0')
    # R2-CAL-3：把 WorldState.shares 作为结构化 base_distribution 传入脊柱推导，约束情景集 + 把概率
    # 限制在建模份额的一个 band 内（除非有依据）。仅当 SIM_DECISION_CHANNEL 开启时生效。
    REPORT_SPINE_ANCHOR_WORLDSTATE = os.environ.get('REPORT_SPINE_ANCHOR_WORLDSTATE', 'true').strip().lower() == 'true'

    # —— EXECPLAN2 第二波改进旋钮（单一真源；各消费方此前经 getattr 读取，这里收口 + 文档化）——
    GRAPH_SEARCH_RECIPE = os.environ.get('GRAPH_SEARCH_RECIPE', 'rrf').strip().lower()          # I-1-0/I-1-6 检索 recipe
    RESEARCH_QUALITY_GATE = os.environ.get('RESEARCH_QUALITY_GATE', 'False').strip().lower() == 'true'  # I-0-3 研究后质量门
    # R2-RES-1：research_quality 分数的软下限——低于此值 run 告警/降信心。修复此前 getattr 孤儿 floor=0
    # 让门双重失效的问题；硬门 RESEARCH_QUALITY_GATE 仍保持 opt-in（默认关），以免稀疏单 actor 问题 wedge。
    RESEARCH_QUALITY_FLOOR = float(os.environ.get('RESEARCH_QUALITY_FLOOR', '0.45') or '0.45')
    PIPELINE_STRICT_SCHEMA = os.environ.get('PIPELINE_STRICT_SCHEMA', 'True').strip().lower() == 'true'  # I-4-4 状态模式版本校验
    # 编排器 RUN 阶段「停滞看门狗」：模拟长时间无轮次推进（区别于慢但在推进）视为卡死，
    # 停模拟并失败，避免管线线程永久空转。秒；<=0 关闭。默认 1800（30 分钟无进展）。
    PIPELINE_RUN_STALL_S = float(os.environ.get('PIPELINE_RUN_STALL_S', '1800') or '1800')
    SIM_EMERGENT_METRICS = os.environ.get('SIM_EMERGENT_METRICS', 'False').strip().lower() == 'true'     # I-2-0 涌现结构指标
    # SIM-12 / OBS-1：默认开——访谈往返 + 人设回退计量开销极小，却让「单薄报告」在多小时跑里可诊断。
    IPC_TELEMETRY_ENABLED = os.environ.get('IPC_TELEMETRY_ENABLED', 'True').strip().lower() == 'true'   # I-5-5 IPC 延迟计量

    ONTOLOGY_TEMPLATE = os.environ.get('ONTOLOGY_TEMPLATE', 'social_opinion').strip().lower()  # I-1-3 领域自适应本体模板
    # 开启后本体层按 prompt 自动在 general_forecast / social_opinion 之间择一（I-1-3）；
    # ONTO-4：默认开——避免把市场/地缘类预测硬塞进社媒 schema；空结果时回退默认模板，
    # 最坏情况等同于今天（degrade-safe）。设 false 可固定用上面的 ONTOLOGY_TEMPLATE。
    ONTOLOGY_AUTO_SELECT = os.environ.get('ONTOLOGY_AUTO_SELECT', 'true').strip().lower() == 'true'
    # 更丰富的本体抽取 prompt（实体分类 archetype/simulation_tier、actor 行为 DNA、valenced 关系）+
    # 保留完整 ontology 对象（CLAUDE/CODEX/GEMINI 三方收敛本体契约）。默认开：新字段缺失时为 no-op，
    # 抽取结果与现状逐字节一致（旧数据/旧测试夹具不受影响）。
    ONTOLOGY_RICH_SCHEMA = os.environ.get('ONTOLOGY_RICH_SCHEMA', 'True').strip().lower() == 'true'
    # NEXTSTEPS P3-2：抽取后对 actors[] 跨轨去重（normalize_name + 双向包含/别名聚类，合并重复
    # 行为规范行并改写关系端点）。默认开：只会收紧 cast（防中心度分裂/重复 persona/salience 污染）；
    # 无重复时为 no-op（与现状一致）。
    CAST_RECONCILE = os.environ.get('CAST_RECONCILE', 'True').strip().lower() == 'true'
    # NEXTSTEPS P3-3：把已实现的 actor 阵容（archetype/relationships[].type）投影成本体种子约束
    # 喂给本体生成（单一真源，避免从散文重新派生导致 schema/instance 漂移）。默认开：把研究确认的
    # 实体类型 + 关系类型作为「保留这些类型、至多再加 2 个领域专属类型、不要重命名」的种子模板注入
    # 本体生成提示词，使本体真正以 actor 阵容 + 关系为底座（与 ONTOLOGY_RICH_SCHEMA 的 archetype/
    # 边族分类元数据互补）。degrade-safe：actor 阵容为空/无类型时 ontology_seed_block 返回 ""，
    # 行为与关闭逐字节一致。如需回到「纯从散文派生本体」可设为 false。
    ONTOLOGY_FROM_DOSSIER = os.environ.get('ONTOLOGY_FROM_DOSSIER', 'True').strip().lower() == 'true'
    PERSONA_EGO_RETRIEVAL = os.environ.get('PERSONA_EGO_RETRIEVAL', 'False').strip().lower() == 'true'   # I-1-5 自我中心人设上下文
    # 人设提示注入 actor 行为 DNA（价值观/信念/激励/资源/风险偏好）+ 关系名册（盟友/对手/竞争者…）。
    # 默认开；仅当 actor 携带 worldview/incentives/resources 等新字段时生效，缺失时为 no-op（与现状一致）。
    PERSONA_BEHAVIORAL_DNA = os.environ.get('PERSONA_BEHAVIORAL_DNA', 'True').strip().lower() == 'true'
    # SIM-7：携带非空 bundled edges/nodes 的人设跳过冗余的 Zep 二次检索（冷实体仍检索）。
    # 此前是 getattr 幽灵旋钮（默认 False）；提升为一等属性并默认开，省去 ~80 个 agent 多数的 1-2 次
    # Zep 搜索。oasis_profile_generator 经 getattr(Config, 'PROFILE_ZEP_SKIP_WHEN_CONTEXT', False) 读取。
    PROFILE_ZEP_SKIP_WHEN_CONTEXT = os.environ.get('PROFILE_ZEP_SKIP_WHEN_CONTEXT', 'True').strip().lower() == 'true'
    API_V1_ENABLED = os.environ.get('API_V1_ENABLED', 'False').strip().lower() == 'true'       # I-9-5 稳定版程序化 API /api/v1
    MODEL_COMPARISON_ENABLED = os.environ.get('MODEL_COMPARISON_ENABLED', 'False').strip().lower() == 'true'  # I-9-4 模型对比
    REPORT_TELEMETRY = os.environ.get('REPORT_TELEMETRY', 'True').strip().lower() == 'true'     # I-5-4 报告级 LLM 计量汇总
    # ITEM-18：完成时把确定性的「Run Telemetry」附录表（stage × 调用/tokens/成本/墙钟）追加到最终报告
    # full_report.md（无 LLM、幂等）。默认开；置 false 关闭附录（run 遥测仍落 telemetry.json / state.options）。
    REPORT_TELEMETRY_APPENDIX = os.environ.get('REPORT_TELEMETRY_APPENDIX', 'True').strip().lower() == 'true'
    # REPORT-3：默认开——把确定性 sim 聚合（top actors/volumes/coalition sizes/P(outcome)/scenario diff）
    # 钉进每章作可引用的数字底座，提升引用覆盖、减少探索式工具调用。
    REPORT_SIGNAL_PACK = os.environ.get('REPORT_SIGNAL_PACK', 'True').strip().lower() == 'true'  # I-3-2 每章注入定量信号包
    # RQ-4：默认 False→True。基线-情景对比表是 what-if 报告的核心可引用工件；仅在有 base
    # 模拟 + 命中「对比/反事实」章节时注入，缺基线时自动 no-op（degrade-safe）。
    REPORT_COMPARISON_TABLE = os.environ.get('REPORT_COMPARISON_TABLE', 'True').strip().lower() == 'true'  # I-3-4 基线-情景对比表
    # R2-KG-7 / RQ-4：确定性「因果骨架」块——以图谱显著度最高的若干 chokepoint 为中心渲染多跳
    # 因果邻域 + 最强 source→outcome 路径，钉进信号包。此前是 getattr 幽灵旋钮（config 从未定义，
    # 在 .env 里设了也无效）；RQ-4 收编为一等属性并默认 True——因果骨架显著抬升报告的机制密度与
    # 引用覆盖。图层多跳遍历有界、任意失败降级为空串，无 actors/无能动角色时为 no-op（degrade-safe）。
    REPORT_CAUSAL_SPINE = os.environ.get('REPORT_CAUSAL_SPINE', 'True').strip().lower() == 'true'
    # 报告背景注入 actor 关系名册 + 激励结构（盟友/对手/竞争者/客户/供应商/出资方…），让叙事更贴角色。
    # 默认开；仅当 actor 携带 relational_roster/incentives 时生效，缺失时为 no-op（与现状一致）。
    REPORT_RELATIONAL_ROSTER = os.environ.get('REPORT_RELATIONAL_ROSTER', 'True').strip().lower() == 'true'
    RECORD_RUN_MANIFEST = os.environ.get('RECORD_RUN_MANIFEST', 'True').strip().lower() == 'true'  # I-8-1 复现清单 run.json

    # —— EXECPLAN2 第三波改进旋钮（剩余 L-effort 新能力；全部默认关，留空即保持当前行为）——
    # 预测质量回归评测开关（EXECPLAN2 I-7-7）：opt-in，绝不进默认 CI。开启后 eval_forecast_quality.py
    # 用 LLM-judge 按 rubric 给固定情景集打分并与 baseline 对比。默认关。
    EVAL_ENABLED = os.environ.get('EVAL_ENABLED', 'False').strip().lower() == 'true'  # I-7-7 预测质量评测

    # —— 运维脚本旋钮（此前各脚本用 getattr(Config, ...) 读但 config.py 从未定义 → "幽灵旋钮"：
    #    在 .env 里设了也无效。这里收口为真实可读项 + .env.example 文档化，消除该反模式）——
    # 定时重跑（scripts/scheduled_rerun.py，NEXTSTEPS P2-4 / I-9-6）。默认关：daemon/tick 才生效。
    SCHEDULER_ENABLED = os.environ.get('SCHEDULER_ENABLED', 'False').strip().lower() == 'true'
    SCHEDULER_TICK_SECONDS = int(os.environ.get('SCHEDULER_TICK_SECONDS', '300') or '300')
    SCHEDULER_MAX_CONCURRENT = int(os.environ.get('SCHEDULER_MAX_CONCURRENT', '1') or '1')
    # 漂移检测阈值（情景概率 |Δ| ≥ 此值即判材料性漂移）+ 漂移 webhook（SSRF 校验后回调）。
    DRIFT_PROB_THRESHOLD = float(os.environ.get('DRIFT_PROB_THRESHOLD', '0.15') or '0.15')
    DRIFT_WEBHOOK_URL = os.environ.get('DRIFT_WEBHOOK_URL', '').strip()
    # 多问题批跑（scripts/batch_runs.py，I-9-3）：单锚点图谱上的最大分叉问题数（成本护栏）。
    BATCH_MAX_FANOUT = int(os.environ.get('BATCH_MAX_FANOUT', '8') or '8')
    BATCH_SHARED_SIMULATION = os.environ.get('BATCH_SHARED_SIMULATION', 'False').strip().lower() == 'true'

    # ============================================================
    # R2 审计修复旋钮汇口（CFG-1 幽灵旋钮收编 + 各组新旗标集中定义）。
    # 全部有安全默认：env 未设时行为与修复前逐字节一致（或按各 finding 的既定默认）。
    # 消费方多为 getattr(Config, NAME, default) 或 env-first 读取，两者与此处定义兼容。
    # ============================================================
    # —— CFG-1：管线编排幽灵旋钮（此前仅 getattr 读、config 从未定义 → .env 设了也无效）——
    # I-4-1 心跳看护：owner 指纹 + 壁钟心跳，让 reconcile 区分「死管线」与「慢但活」。
    PIPELINE_HEARTBEAT_ENABLED = os.environ.get('PIPELINE_HEARTBEAT_ENABLED', 'true').strip().lower() == 'true'
    PIPELINE_HEARTBEAT_INTERVAL_S = float(os.environ.get('PIPELINE_HEARTBEAT_INTERVAL_S', '30') or '30')
    PIPELINE_HEARTBEAT_STALE_S = float(os.environ.get('PIPELINE_HEARTBEAT_STALE_S', '120') or '120')
    # I-5-6 状态 API 的 staleness 判定与 ETA 外推上限。
    PIPELINE_STALE_S = float(os.environ.get('PIPELINE_STALE_S', '300') or '300')
    PIPELINE_ETA_CAP_S = float(os.environ.get('PIPELINE_ETA_CAP_S', '7200') or '7200')
    # I-4-6 运行中临时产物深链 + 扫描节流；I-4-3 复用前产物完整性校验。
    PIPELINE_LIVE_ARTIFACTS = os.environ.get('PIPELINE_LIVE_ARTIFACTS', 'true').strip().lower() == 'true'
    PIPELINE_PARTIAL_SCAN_EVERY_S = float(os.environ.get('PIPELINE_PARTIAL_SCAN_EVERY_S', '10') or '10')
    PIPELINE_VALIDATE_ARTIFACTS = os.environ.get('PIPELINE_VALIDATE_ARTIFACTS', 'true').strip().lower() == 'true'
    # VIZ-2 可视化产物通道：把 handoff/charts/*.png|svg(+charts.json 清单) 与 handoff/data/*.csv
    # 纳入产物 specs（→ manifest 完整性登记 + 运行中 *_partial 深链）。老跑不产出这些文件时，
    # 各机制按「文件缺失即跳过」自然降级，从不误伤健康/完整性校验。默认 true（缺文件即 no-op）。
    PIPELINE_VIZ_ARTIFACTS = os.environ.get('PIPELINE_VIZ_ARTIFACTS', 'true').strip().lower() == 'true'
    # I-8-1 run.json 是否附带关键包版本（pip 枚举有开销，默认关）。
    MANIFEST_CAPTURE_VERSIONS = os.environ.get('MANIFEST_CAPTURE_VERSIONS', 'false').strip().lower() == 'true'
    # SIM-11 persona 并行扇出（HTTP 提供方；CLI 固定 3）。
    PARALLEL_PROFILE_COUNT = int(os.environ.get('PARALLEL_PROFILE_COUNT', '16') or '16')
    # R2-EXEC-7 研究阶段后台预热嵌入器；R2-RES-7 as_of 双时态锚校验；建图输入源。
    EMBED_WARM_AT_RESEARCH = os.environ.get('EMBED_WARM_AT_RESEARCH', 'false').strip().lower() == 'true'
    VALIDATE_AS_OF_DATE = os.environ.get('VALIDATE_AS_OF_DATE', 'true').strip().lower() == 'true'
    GRAPH_CHUNK_SOURCE = os.environ.get('GRAPH_CHUNK_SOURCE', 'both').strip().lower()  # both|dossier_only|report_only

    # —— ORCH-2/ORCH-8/XRUN-3：编排器/CLI 鲁棒性 ——
    # 启动回收前先探测本机端口是否已有后端在服务：占用则整体跳过孤儿回收（注定 Address-in-use
    # 而死的重复进程不得破坏活进程拥有的管线状态）。
    PIPELINE_RECLAIM_PORT_PROBE = os.environ.get('PIPELINE_RECLAIM_PORT_PROBE', 'true').strip().lower() == 'true'
    # 报告阶段起飞前 ~10 token 探测主/回退提供方可用性；双双不可用时 <60s 内以可恢复的
    # REPORT 阶段失败收场，而不是烧完全部章节成本后才被健康门拦下。
    REPORT_LLM_PREFLIGHT = os.environ.get('REPORT_LLM_PREFLIGHT', 'true').strip().lower() == 'true'
    # claude CLI 子进程隔离操作员的全局 ~/.claude hooks（--settings 内联 disableAllHooks，
    # 保留 OAuth；--bare 会丢登录态故不用）。SessionEnd 钩子曾让 2769+ 次管线调用空错误失败。
    LLM_CLI_ISOLATE_HOOKS = os.environ.get('LLM_CLI_ISOLATE_HOOKS', 'true').strip().lower() == 'true'

    # —— 研究组（RES-*；deerflow bridge 在自己的 venv 里直读 os.environ，此处定义仅为
    #    配置面单一真源 + 编排器侧镜像守卫消费；bridge 不 import Config）——
    ACTOR_SYNTH_MIN_CONTEXT_CHARS = int(os.environ.get('ACTOR_SYNTH_MIN_CONTEXT_CHARS', '3000') or '3000')
    RESEARCH_MIN_REPORT_CHARS = int(os.environ.get('RESEARCH_MIN_REPORT_CHARS', '400') or '400')
    RESEARCH_FETCH_ACCOUNTING_V2 = os.environ.get('RESEARCH_FETCH_ACCOUNTING_V2', 'true').strip().lower() == 'true'
    RESEARCH_ASOF_MAX_LAG_DAYS = int(os.environ.get('RESEARCH_ASOF_MAX_LAG_DAYS', '45') or '45')
    RESEARCH_QUALITY_GROUNDING = os.environ.get('RESEARCH_QUALITY_GROUNDING', 'true').strip().lower() == 'true'
    ACTOR_DOSSIER_JUDGE_STRICT = os.environ.get('ACTOR_DOSSIER_JUDGE_STRICT', 'false').strip().lower() == 'true'
    RESEARCH_COVERAGE_GATE_STANDARD = os.environ.get('RESEARCH_COVERAGE_GATE_STANDARD', 'false').strip().lower() == 'true'

    # —— 本体/准备组（ONT-1/PREP-*/XRUN-10；utils/actors.py 与 config 生成器经 getattr 读取）——
    ONTOLOGY_SEED_ADAPTIVE_BUDGET = os.environ.get('ONTOLOGY_SEED_ADAPTIVE_BUDGET', 'true').strip().lower() == 'true'
    SIM_EVENT_REACT_BUFFER = os.environ.get('SIM_EVENT_REACT_BUFFER', 'true').strip().lower() == 'true'
    SIM_SCHEDULE_CLAMP_ROUNDS = os.environ.get('SIM_SCHEDULE_CLAMP_ROUNDS', 'true').strip().lower() == 'true'
    SIM_RULE_FALLBACK_STANCE = os.environ.get('SIM_RULE_FALLBACK_STANCE', 'true').strip().lower() == 'true'
    SIM_SEED_POST_VARIANTS = os.environ.get('SIM_SEED_POST_VARIANTS', 'true').strip().lower() == 'true'
    PERSONA_FIELD_EXTRACTION = os.environ.get('PERSONA_FIELD_EXTRACTION', 'true').strip().lower() == 'true'

    # —— 图谱组（KG-*/ONT-7；graph_builder / zep_entity_reader / report 检索经 getattr 读取）——
    GRAPH_CHOKEPOINT_PRIORS = os.environ.get('GRAPH_CHOKEPOINT_PRIORS', 'false').strip().lower() == 'true'
    try:
        GRAPH_CHOKEPOINT_MAX_NODES = int(os.environ.get('GRAPH_CHOKEPOINT_MAX_NODES', '1500') or '1500')
    except ValueError:
        GRAPH_CHOKEPOINT_MAX_NODES = 1500
    if GRAPH_CHOKEPOINT_MAX_NODES <= 0:
        GRAPH_CHOKEPOINT_MAX_NODES = 1500
    GRAPH_PRIORS_ALIAS_FOLD = os.environ.get('GRAPH_PRIORS_ALIAS_FOLD', 'true').strip().lower() == 'true'
    GRAPH_MAX_SKIPPED_RATIO = float(os.environ.get('GRAPH_MAX_SKIPPED_RATIO', '0.3') or '0.3')
    SIM_TIER_FROM_ONTOLOGY = os.environ.get('SIM_TIER_FROM_ONTOLOGY', 'false').strip().lower() == 'true'
    # KG-2: 图谱检索查询长度上限。0=自动（GRAPHITI_REMOTE=true 时 380，本地后端 1200）。
    GRAPH_QUERY_MAX_CHARS = int(os.environ.get('GRAPH_QUERY_MAX_CHARS', '0') or '0')
    GRAPH_QUERY_CLAMP_SEMANTIC = os.environ.get('GRAPH_QUERY_CLAMP_SEMANTIC', 'true').strip().lower() == 'true'

    # —— 运行环组（RUN-*/XRUN-*；run_parallel_simulation.py 以 env-first 再 getattr 读取）——
    SIM_START_HOUR = int(os.environ.get('SIM_START_HOUR', '0') or '0')          # 0-23；time_config.start_hour 优先
    SIM_RECENCY_CARRY = os.environ.get('SIM_RECENCY_CARRY', 'false').strip().lower() == 'true'
    SIM_TWITTER_MODEL_FREE_FEED = os.environ.get('SIM_TWITTER_MODEL_FREE_FEED', 'true').strip().lower() == 'true'
    SIM_TOOL_ARG_NORMALIZE = os.environ.get('SIM_TOOL_ARG_NORMALIZE', 'true').strip().lower() == 'true'
    SIM_IDLE_CLOSE_MIN = float(os.environ.get('SIM_IDLE_CLOSE_MIN', '60') or '60')  # <=0 = 无限等待
    SIM_LLM_ERROR_RATE_THRESHOLD = float(os.environ.get('SIM_LLM_ERROR_RATE_THRESHOLD', '0.5') or '0.5')
    SIM_RESUME = os.environ.get('SIM_RESUME', 'false').strip().lower() == 'true'
    # ITEM 3: 轮级检查点写入开关（默认开）。开启时每轮原子落盘 <platform>/checkpoint.json
    # （含 config_hash + python-random RNG 状态），使任意崩溃/被杀/后端重启后的运行可被续跑；
    # 续跑本身仍由 SIM_RESUME/--resume 触发。置 false → 不产出 checkpoint.json（RUN-7 早期 degrade-safe 行为）。
    SIM_CHECKPOINT = os.environ.get('SIM_CHECKPOINT', 'true').strip().lower() == 'true'
    INTERVIEW_TIMEOUT_PER_AGENT = float(os.environ.get('INTERVIEW_TIMEOUT_PER_AGENT', '30') or '30')
    SIM_CLI_TOOL_EMULATION = os.environ.get('SIM_CLI_TOOL_EMULATION', 'true').strip().lower() == 'true'
    SIM_LLM_FALLBACK = os.environ.get('SIM_LLM_FALLBACK', 'true').strip().lower() == 'true'
    SIM_MIN_FREE_DISK_GB = float(os.environ.get('SIM_MIN_FREE_DISK_GB', '2') or '2')  # <=0 关闭
    SIM_STEP_FAILURE_LIMIT = int(os.environ.get('SIM_STEP_FAILURE_LIMIT', '3') or '3')  # 0 = 旧的无限跳轮
    # ITEM 20 模拟真实感四件套（均 env 门控、默认安全、可降级）：
    # (1) 参与度采样：每轮有机动作后从活跃 agent 确定性采样 LIKE_POST 落到本轮新帖上（权重∝该帖
    #     本轮已获互动），补足 OASIS 默认 feed「满屏帖 0 赞」的纯广播失真。采样赞标记 is_engagement_sample，
    #     供有机比例侦测器排除（诚实：采样赞不掩盖 agent 自身零点赞）。rate=每个活跃者本轮产生一次赞的概率。
    SIM_ENGAGEMENT_SAMPLER = os.environ.get('SIM_ENGAGEMENT_SAMPLER', 'true').strip().lower() == 'true'
    SIM_ENGAGEMENT_RATE = float(os.environ.get('SIM_ENGAGEMENT_RATE', '0.3') or '0.3')  # [0,1] clamp
    # (3) 种子 FOLLOW 风暴节流：每个 follower 的种子 FOLLOW 动作上限（add_edge 建图不受影响，仅截断
    #     写 trace/follow 表的 FOLLOW 动作），避免 630 条种子关注淹没早期动作日志。<=0 = 不限（旧行为）。
    SIM_MAX_FOLLOWS_PER_AGENT_ROUND = int(os.environ.get('SIM_MAX_FOLLOWS_PER_AGENT_ROUND', '3') or '3')
    # (2) 有机比例塌缩侦测：逐平台逐轮跟踪 post:comment:like，连续 ≥K 轮 posts>0 而 comments+likes==0
    #     时把结构化告警写入 run_summary（检测+诚实优先，绝不伪造互动）。
    SIM_ORGANIC_RATIO_DETECTOR = os.environ.get('SIM_ORGANIC_RATIO_DETECTOR', 'true').strip().lower() == 'true'
    SIM_ORGANIC_RATIO_MIN_CONSECUTIVE = int(os.environ.get('SIM_ORGANIC_RATIO_MIN_CONSECUTIVE', '3') or '3')

    # —— 报告组（RPT-*/XRUN-1/XRUN-5/RPT-6/RPT-8；report_agent / forecast_extractor 经 getattr 读取）——
    REPORT_ABORT_ON_LLM_OUTAGE = os.environ.get('REPORT_ABORT_ON_LLM_OUTAGE', 'true').strip().lower() == 'true'
    REPORT_SECTION_RETRY_MAX = int(os.environ.get('REPORT_SECTION_RETRY_MAX', '2') or '2')  # RQ-1 1→2；0=旧的无重试
    REPORT_SECTION_RETRY_BACKOFF_S = float(os.environ.get('REPORT_SECTION_RETRY_BACKOFF_S', '8.0') or '8.0')
    REPORT_CRITIQUE_BEFORE_PROSE = os.environ.get('REPORT_CRITIQUE_BEFORE_PROSE', 'true').strip().lower() == 'true'
    FORECAST_BINARY_CONTRARIAN = os.environ.get('FORECAST_BINARY_CONTRARIAN', 'true').strip().lower() == 'true'
    FORECAST_SIM_SENSITIVITY = os.environ.get('FORECAST_SIM_SENSITIVITY', 'true').strip().lower() == 'true'
    FORECAST_BINARY_THEMES = os.environ.get('FORECAST_BINARY_THEMES', '').strip()  # 空=由 brief/主题自适应
    FORECAST_HORIZON_CHECK = os.environ.get('FORECAST_HORIZON_CHECK', 'true').strip().lower() == 'true'  # RQ-6 需求↔二元预测结算年份一致性标记
    # ITEM 12：多模型二元预测集成。逗号分隔的（副）提供方名清单（如 'openai,deepseek,glm'）；
    # 空=关闭（默认，逐字节复现旧单模型行为）。开启时对每个所列提供方各跑一次同提示词二元抽取，
    # 按 id/陈述匹配同一条预测，用与种子集成同一套 extremizing log-odds（ENSEMBLE_EXTREMIZE_A）
    # 把各模型概率池化为发布概率，记 binary['ensemble']={models,probs,pooled,spread}。任一副提供方
    # 构造/抽取失败仅跳过并记 flag，绝不阻断主抽取。
    FORECAST_ENSEMBLE_MODELS = os.environ.get('FORECAST_ENSEMBLE_MODELS', '').strip()
    # 跨模型概率样本 stdev 超此阈值 → 该预测记入 low-agreement（binary_quality.ensemble），
    # 二元预测表渲染以 ±spread 显示分歧。默认 0.15。
    FORECAST_ENSEMBLE_SPREAD_THRESHOLD = float(os.environ.get('FORECAST_ENSEMBLE_SPREAD_THRESHOLD', '0.15') or '0.15')
    REPORT_QUOTE_AUDIT_V2 = os.environ.get('REPORT_QUOTE_AUDIT_V2', 'true').strip().lower() == 'true'
    REPORT_COMPACT_RETRIEVAL_QUERY = os.environ.get('REPORT_COMPACT_RETRIEVAL_QUERY', 'true').strip().lower() == 'true'
    # RQ-2 报告修复门：质量门失败时按维度单次定向修复（引用回填 / 引文接地 / 占位符解析），
    # 然后重跑审计一次并把 before/after 记进 forecast['quality']['repair']（合并，不覆盖）。默认开；
    # 任一步失败仅告警（degrade-safe），绝不影响主报告。
    REPORT_REPAIR_PASSES = os.environ.get('REPORT_REPAIR_PASSES', 'true').strip().lower() == 'true'
    # RQ-2 成稿语言纯度扫描：检测非 CJK 目标报告中的 CJK 片段（反之亦然），一次批量 LLM 调用内联翻译，
    # 引用型原文以括注/脚注保留。默认开；任何错误 degrade-safe 跳过（保留原文）。
    REPORT_LANGUAGE_PURITY = os.environ.get('REPORT_LANGUAGE_PURITY', 'true').strip().lower() == 'true'
    # RQ-5 每章反思：草稿通过基本有效性后做一次廉价批判（骨架概率一致性 / 硬数字接地 / 篇幅下限 /
    # 不复述前序章节），返回 PASS 或单条修订指令；至多一次修订抽取。轮数由 MAX_REFLECTION_ROUNDS 上限。
    REPORT_SECTION_REFLECTION = os.environ.get('REPORT_SECTION_REFLECTION', 'true').strip().lower() == 'true'

    # —— BILINGUAL：双语报告（自动生成另一语种版本）——
    # 报告生成末尾（finalize/可视化/纯度之后）自动生成成稿的「另一语种」版本：英文报告 → 简体中文
    # 版，中文报告 → analyst-grade 英文版。按 H2（'## '）章节边界切块、并发逐章翻译（严格保留
    # markdown 结构 / 表格列数 / 代码 & mermaid 围栏原样 / 图片 URL / 引用标记 / 数字概率逐字节），
    # 落 reports/{id}/full_report.{en|zh}.md 并把 translations 条目写入 meta。完全 degrade-safe：
    # 任何失败/非中英文脚本 → 跳过，主交付物（full_report.md）绝不受影响。默认开；设 false 关闭。
    REPORT_BILINGUAL = os.environ.get('REPORT_BILINGUAL', 'true').strip().lower() == 'true'
    # 逐章节翻译的并发度（ThreadPoolExecutor 线程数）；下限 1（串行）。默认 4。
    REPORT_TRANSLATION_CONCURRENCY = max(1, int(os.environ.get('REPORT_TRANSLATION_CONCURRENCY', '4') or '4'))

    # —— PM-2：确定性逐预测市场锚定（forecast_extractor 经 getattr 读取）——
    # 抽取二元预测后跑一次批处理 LLM 匹配（陈述表 × 相关性门控市场表），确定性回填
    # rich market_anchor（隐含概率取我们的快照、divergence 本地计算）。默认开；关闭则回到
    # 「仅模型自愿转录 market_anchor」的 opt-in 行为（取证 0/13 命中，degrade-safe）。
    FORECAST_MARKET_ANCHORING = os.environ.get('FORECAST_MARKET_ANCHORING', 'true').strip().lower() == 'true'
    # 锚定采纳的最小 resolution_equivalence 严格度：exact|near|loose（默认 near，即采纳
    # exact/near、丢弃 loose 的宽泛主题匹配，避免把不同结算口径的市场硬贴成锚点）。
    FORECAST_MARKET_ANCHOR_MIN_EQUIVALENCE = os.environ.get('FORECAST_MARKET_ANCHOR_MIN_EQUIVALENCE', 'near').strip().lower()
    # 10pp 规则：锚定后 |model_p − market_p|>0.10 且理由未提及市场的预测，做一次有界重述，
    # 须在理由中引用市场或有依据地保留分歧（绝不静默移动概率）。默认开；关闭=不重述。
    FORECAST_MARKET_DIVERGENCE_REVISION = os.environ.get('FORECAST_MARKET_DIVERGENCE_REVISION', 'true').strip().lower() == 'true'

    # —— RQ-2：成稿后抽取切片（head+tail，结论在文末）+ 抽取 max_tokens（forecast_extractor 经 getattr 读取）——
    # 情景/二元抽取此前只取正文开头 [:budget]，把文末的收敛判断切掉；改为「前 head_ratio +
    # 后 (1-head_ratio)」两段拼接。默认值即新行为；调小 head_ratio 偏向结论、调大偏向导言。
    FORECAST_EXTRACT_BUDGET = int(os.environ.get('FORECAST_EXTRACT_BUDGET', '40000') or '40000')
    FORECAST_BINARY_EXTRACT_BUDGET = int(os.environ.get('FORECAST_BINARY_EXTRACT_BUDGET', '48000') or '48000')
    FORECAST_EXTRACT_HEAD_RATIO = float(os.environ.get('FORECAST_EXTRACT_HEAD_RATIO', '0.6') or '0.6')
    # 情景抽取 max_tokens 2048→4096（5 情景 × anchor/rationale/criteria 常被 2048 截断成断裂 JSON）。
    FORECAST_EXTRACT_MAX_TOKENS = int(os.environ.get('FORECAST_EXTRACT_MAX_TOKENS', '4096') or '4096')

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
        # LLM-6: native_tools=False——MiniMax-M3 的 agentic 工具调用不可靠（0-tool-call 推理
        # 残段烧掉工具预算后才被 ReAct 兜底救回）；缺省键=True，其余 openai-compat 提供方不受影响。
        # 验证可靠后删除此键即可恢复；LLM_NATIVE_TOOLS_PROVIDERS 环境变量可整体覆盖。
        'minimax':    {'label': 'MiniMax 代码计划（国内版）', 'needs_key': True,  'deerflow_model': 'minimax', 'openai_compat': True,
                       'default_base': _MINIMAX_DEFAULT_BASE_URL, 'default_model': _MINIMAX_DEFAULT_MODEL, 'key_env': 'MINIMAX_API_KEY',
                       'native_tools': False},
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
    # GRAPH-9 / GRAPH-11：4→2。瞬态错误重试预算降到 2，避免一个慢/卡死的 op 在失败前耗掉
    # 4×op_timeout；与受限的 op 超时配合把失败延迟收紧。
    ZEP_MAX_RETRIES = int(os.environ.get('ZEP_MAX_RETRIES', '2'))
    ZEP_RETRY_DELAY_SECONDS = float(os.environ.get('ZEP_RETRY_DELAY_SECONDS', '2.0'))
    ZEP_RATE_LIMIT_BUFFER_SECONDS = float(os.environ.get('ZEP_RATE_LIMIT_BUFFER_SECONDS', '1.0'))
    ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS = float(os.environ.get('ZEP_RATE_LIMIT_MAX_SLEEP_SECONDS', '90.0'))
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    # CHUNK-1 / GRAPH-2 / ONTO-1 / ONTO-2 / RESEARCH-1：500→2500 chars/episode。
    # ~400 个微 episode 是 5 小时建图的根因；2.5K chars 把相关三元组留在同一窗口，即便
    # concurrency=1 也把 episode 数砍 ~5x，且提升关系召回。orchestrator/本体分块共用此默认。
    DEFAULT_CHUNK_SIZE = int(os.environ.get('DEFAULT_CHUNK_SIZE', '2500') or '2500')  # 默认切块大小（chars/graph episode）
    # CHUNK-1 / GAP-3：50→250（~10% chunk_size，按更大块等比放大），保证实体不在边界被切断又不大量重抽。
    DEFAULT_CHUNK_OVERLAP = int(os.environ.get('DEFAULT_CHUNK_OVERLAP', '250') or '250')  # 默认重叠大小
    
    # OASIS模拟配置
    # T3.7: 0 = 不截断（跑满按 total_hours/minutes_per_round 算出的完整轮数，如 72h/60min=72 轮）。
    # 设为正整数则作为全局轮数上限（每次运行可被 options.max_rounds 覆盖；冒烟测试用小值）。
    # SIM-2：0→36。封顶一个 config-gen 产出的病态 336 轮（~9x）；options.max_rounds 仍可为长时域覆盖。
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '36'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # —— OASIS 并发上限（每轮在飞 LLM 请求数）单一真源（EXECPLAN2 I-8-4）——
    # 此前这两个旋钮只在 utils/oasis_llm.py::get_oasis_semaphore 里经 os.environ 直读，
    # 绕过了集中式 Config 配置面——对 doctor/validate/run 清单不可见、无法记录复现。
    # 提升为一等 Config 属性，默认值与 oasis_llm.py 的 DEFAULT_*_SEMAPHORE 逐字节一致
    # （CLI 提供方 8、OpenAI 兼容提供方 30），故 env 未设时行为字节稳定不变。
    # CLI 提供方(claude-cli/codex-cli)：每个调用 spawn 子进程，8 是吞吐与负载的稳妥平衡。
    OASIS_CLI_SEMAPHORE = int(os.environ.get('OASIS_CLI_SEMAPHORE', '8') or '8')
    # OpenAI 兼容提供方：纯 HTTP 并发。SIM-3：默认 24（在飞 agent LLM 调用数，//platforms 分摊）；
    # 从保守值 16→24→32 逐档 ramp 盯 p95，而非一步到 64。
    OASIS_SEMAPHORE = int(os.environ.get('OASIS_SEMAPHORE', '24') or '24')
    
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
    # T4.4/RQ-1: 每章最多工具调用。默认 8→12——展开后的长章节（目标 3000-6000 字 + 2-4 个
    # ### 子小节）需要更多实证检索轮次才能填满机制密度；小 page_budget 的报告经 derive_report_shape
    # 收敛回 8（见 report_agent._report_shape）。运维可下调以省成本。
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '12'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    # 章节正文生成的输出 token 上限（OpenAI 兼容提供方生效；CLI 提供方由 prompt 篇幅下限驱动）。
    # 此前硬编码 8192，会截断长章节正文。默认提升到 32768——gemini-3.5-flash 等大输出模型
    # （65536 输出上限）可写出更完整的分析章节；需要更省/更短可调低。强制收尾兜底仍受此约束。
    REPORT_AGENT_SECTION_MAX_TOKENS = int(os.environ.get('REPORT_AGENT_SECTION_MAX_TOKENS', '32768') or '32768')
    # REPORT-9：中间「工具选择」回合的较小补全预算。32768 用在决定工具的回合上会诱发冗长推理；
    # 仅压中间回合，最终答案回合仍用 REPORT_AGENT_SECTION_MAX_TOKENS（32768），不缩短成稿章节长度。
    REPORT_AGENT_TOOL_TURN_MAX_TOKENS = int(os.environ.get('REPORT_AGENT_TOOL_TURN_MAX_TOKENS', '8192') or '8192')
    # RQ-1：章节篇幅契约（展开默认）。REPORT_SECTION_TARGET_CHARS 为 'lo-hi' 目标区间（字符数，
    # 模板进 SECTION_SYSTEM/USER_PROMPT），REPORT_SECTION_FLOOR_CHARS 为硬下限。默认 3000-6000 /
    # 下限 2000，取代旧的 1800-2800 / 1500。小 page_budget 报告经 derive_report_shape 收敛回旧的
    # 紧凑值（1800-2800 / 1500），故此处仅设「无/大 page_budget」时采用的展开上限。解析失败回退默认。
    REPORT_SECTION_TARGET_CHARS = os.environ.get('REPORT_SECTION_TARGET_CHARS', '3000-6000').strip()
    REPORT_SECTION_FLOOR_CHARS = int(os.environ.get('REPORT_SECTION_FLOOR_CHARS', '2000') or '2000')

    @classmethod
    def report_section_target_chars(cls):
        """把 REPORT_SECTION_TARGET_CHARS('lo-hi') 解析成 (lo, hi) 两个正整数；
        任何解析失败（缺分隔符/非数字/lo>=hi）回退到展开默认 (3000, 6000)（degrade-safe）。"""
        raw = (cls.REPORT_SECTION_TARGET_CHARS or '').strip()
        try:
            lo_s, hi_s = raw.split('-', 1)
            lo, hi = int(lo_s.strip()), int(hi_s.strip())
            if lo > 0 and hi > lo:
                return lo, hi
        except (ValueError, AttributeError):
            pass
        return 3000, 6000

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
    # 研究深度：quick / standard / deep。CONF-1：默认 standard→deep——deep 的多轮调研协议
    # （source map → primary evidence → contradictions → synthesis）是报告证据密度的最大杠杆。
    DEERFLOW_RESEARCH_DEPTH = os.environ.get('DEERFLOW_RESEARCH_DEPTH', 'deep').strip().lower()
    # 研究报告/结构化输出语言。CONF-1：默认 Chinese→空 = 不传 --target-language，由模型按
    # brief 自动检测语言（英文 brief → 英文报告）。所有消费方均 None-safe：编排器空值不加
    # CLI 参数；report_agent 先走 detect_output_language(brief) 再回退。显式设值仍强制该语言。
    DEERFLOW_RESEARCH_LANGUAGE = os.environ.get('DEERFLOW_RESEARCH_LANGUAGE', '').strip() or None
    # 研究阶段最长等待秒数（仅作为兜底/显式覆盖；正常由研究深度自适应）
    DEERFLOW_RESEARCH_TIMEOUT = int(os.environ.get('DEERFLOW_RESEARCH_TIMEOUT', '10800'))
    # 是否启用 DeerFlow 子代理（并行 scoped workers，更深但更慢）。CONF-1：默认 false→true——
    # 并行 scoped workers 的覆盖增益远超时延成本（时延由 deerflow_depth_budget 的 ×1.5 兜住）。
    DEERFLOW_SUBAGENTS = os.environ.get('DEERFLOW_SUBAGENTS', 'true').strip().lower() == 'true'
    # 深度研究 per-KIQ / per-actor 子代理扇出（EXECPLAN2 I-0-4）：开场 scope pass 产出种子清单后，
    # 并行派发若干 scoped 子调查，合并工作笔记再做矛盾核验+综合。CONF-1：默认 false→true——
    # per-KIQ 扇出显著抬高证据覆盖；关闭则回到线性协议。经 env 下发给 deerflow 子进程（独立 venv）读取。
    RESEARCH_DEEP_FANOUT = os.environ.get('RESEARCH_DEEP_FANOUT', 'true').strip().lower() == 'true'
    # 扇出宽度上限（并行子调查数）；防止子代理把工具/LLM 预算放大失控。CONF-1：4→8 与扇出默认开配套。
    RESEARCH_FANOUT_WIDTH = int(os.environ.get('RESEARCH_FANOUT_WIDTH', '8') or '8')
    # PAR-2：编排器级「多角度并行研究轨」。>1 时研究阶段并行跑 K 个 DeerFlowResearchRunner
    # 子进程，每个带角度特化前缀（轨1=基线证据扫描，即原始 brief 逐字；轨2=基率/参照类/历史
    # 类比；轨3=行为者激励+反面证伪+市场定价），各写入 handoff/track_<k>/，随后确定性合并回
    # handoff/（报告按 '# Track N' H1 拼接、来源按 URL 去重保最高层级、actors 走 reconcile_cast、
    # quantitative/contested/timeline 拼接去重、research_quality 取各轨最小值、预测市场取最新）。
    # 各轨看门狗用既有 per-run 预算故 wall-clock ≈ 1 轨。默认 3；某轨失败 → 用存活轨（≥1）继续
    # 并打降级标；全失败 → 阶段失败（与今日一致）。设 1 = 今日单轨路径逐字节一致（degrade-safe）。
    # 上限受已定义角度数约束（当前 3）；>3 的值被夹到 3。
    RESEARCH_PARALLEL_TRACKS = max(1, int(os.environ.get('RESEARCH_PARALLEL_TRACKS', '3') or '3'))
    # 双轨研究：在研究阶段「同时」跑两套调研工作流——Track A = 既有 deep-research 工作流
    # （deep-research skill）产出 research_report.md（广覆盖证据报告，角色不变）；Track B =
    # 新增 actor-ontology 研究工作流（actor-ontology-research skill）产出 actor_dossier.md
    # （以 actor 为中心、面向本体的卷宗：archetype/simulation_tier/role/价值观/信念/激励/资源、
    #  有向且带 valence 的关系、历史演变、情势简报）。随后 Track B 卷宗作为「主」actor 来源喂给
    # 本体生成 + actor 抽取；Track A 报告作为「附加上下文」增强知识图谱/本体/人设上下文/情势上下文。
    # 默认开；关闭（或 Track B 失败/空）时行为与现状逐字节一致（单跑 Track A，不落 actor_dossier.md）。
    # 注意：开启后研究阶段 LLM 成本约翻倍（两套工作流并行），如需省额度可设为 false 关闭。
    DEERFLOW_DUAL_TRACK = os.environ.get('DEERFLOW_DUAL_TRACK', 'True').strip().lower() == 'true'

    # ============================================================
    # EXECPLAN —— 打通「研究 → 图谱 → 模拟 → 报告」结构化契约的旋钮
    # 默认值保持「当前行为」；所有新字段可选降级（缺失即回退旧路径）。
    # ============================================================
    # --- 图谱（Phase 2）---
    # 文本抽取前，把研究确认的 actors + relationships 作为 typed 边种入图谱（T2.2）
    GRAPH_SEED_FROM_ACTORS = os.environ.get('GRAPH_SEED_FROM_ACTORS', 'true').strip().lower() == 'true'
    # episode 并发抽取数（>1 提速，但有轻微 dedup 排序风险；1 = 与旧行为逐字节一致）(T2.5)
    # GRAPH-1：1→4。串行 concurrency=1 是 ~5h 的主瓶颈；5h 内零 429 说明代理容得下并行负载。
    # 必须与 GRAPH_RESOLVE_ENTITIES=true 配对，用 post-build 合并兜住并行建图的 dedup race。
    GRAPH_BUILD_CONCURRENCY = int(os.environ.get('GRAPH_BUILD_CONCURRENCY', '4'))
    # GRAPH-3：graphiti 单 episode 内 per-node/edge 的 LLM 扇出（graphiti 默认 20；此前 effective 8
    # 把 intra-episode 并行压到 ~3-4 波）。16 拓宽扇出，与 concurrency 联合 sizing。runtime.py 经 env 读取，
    # 故模块底部用 os.environ.setdefault 把该默认下发给只读 env 的 runtime（见文件末尾）。
    GRAPHITI_MAX_COROUTINES = int(os.environ.get('GRAPHITI_MAX_COROUTINES', '16') or '16')
    # R2-EXEC-1：graphiti 阻塞式 LLM HTTP I/O 专用线程池大小。没有专用 I/O executor 时，round-1 的
    # 并发旋钮会被共享 cpu+4 默认池（~20）硬封顶；64 ≈ concurrency × coroutines + headroom。
    GRAPH_LLM_EXECUTOR_WORKERS = int(os.environ.get('GRAPH_LLM_EXECUTOR_WORKERS', '64') or '64')
    # R2-EXEC-2：SentenceTransformer encode 的独立小算力池，避免 CPU-bound 编码抢占 LLM I/O 线程
    # 并在 GIL 上串行化。保持小（4），仅隔离编码工作负载。
    EMBED_EXECUTOR_WORKERS = int(os.environ.get('EMBED_EXECUTOR_WORKERS', '4') or '4')
    # R2-EXEC-5 / GAP-2：持久化写穿嵌入缓存（键=(model, normalized_text)），跨 resume/seed 复用。
    # 留空=落在 GRAPHITI_DATA_DIR/embed_cache.sqlite（即文档里的 uploads/graphiti_db/embed_cache.sqlite）。
    EMBED_DISK_CACHE_PATH = (
        os.environ.get('EMBED_DISK_CACHE_PATH', '').strip()
        or os.path.join(GRAPHITI_DATA_DIR, 'embed_cache.sqlite')
    )
    # GRAPH-6：zep_paging 节点读取上限（此前硬编码 2000）。~400-episode 语料的节点数会超过 2000 而被
    # 静默截断，连带影响中心度/分量/dedup/agent 选池。8000 防止该静默截断。
    GRAPH_MAX_NODES = int(os.environ.get('GRAPH_MAX_NODES', '8000') or '8000')
    # 建图末尾跑 Leiden 社区发现（派系/联盟，best-effort）(T2.4 / NEXTSTEPS P3-9)。
    # ⚠️ 默认关：graphiti_core 的 label_propagation（community_operations.py:102 `while True`）在
    # 某些图拓扑上不收敛 → 100% CPU 死循环，且因运行在 graphiti 单一后台事件循环上、是纯同步 CPU 段，
    # 无法被 op-timeout 取消，会拖垮 build 之后的所有读/操作（实测全部 900s 超时、管线在 prepare 失败）。
    # 历次运行社区数恒为 0（本工作负载无价值）。待 graphiti_core 修复 label_propagation 收敛性后再开。
    # 关闭后 GRAPH_COMMUNITY_RETRIEVAL 自动跟随关闭，faction_brief 退回行为日志启发式（既有降级路径）。
    GRAPH_BUILD_COMMUNITIES = os.environ.get('GRAPH_BUILD_COMMUNITIES', 'false').strip().lower() == 'true'
    # NEXTSTEPS P3-7：把建图时算出的度数中心度（此前丢弃）作为影响力先验融入 agent-cap 的
    # salience 排序——高中心度=更枢纽，比原始提及数更接地。默认开；无 graph_priors 时 +0.0（no-op）。
    GRAPH_CENTRALITY_PRIORS = os.environ.get('GRAPH_CENTRALITY_PRIORS', 'true').strip().lower() == 'true'
    # 把已发现的社区(派系)做成一等可检索结构：报告侧 faction_brief 工具 + 人设侧群体身份
    # （EXECPLAN2 I-1-2）。默认跟随 GRAPH_BUILD_COMMUNITIES（无社区节点时 faction_brief 自动
    # 降级到 coalition_map 的行为日志聚类）。显式设 true/false 可覆盖。
    GRAPH_COMMUNITY_RETRIEVAL = (
        os.environ.get('GRAPH_COMMUNITY_RETRIEVAL', '').strip().lower() == 'true'
        if os.environ.get('GRAPH_COMMUNITY_RETRIEVAL', '').strip() != ''
        else GRAPH_BUILD_COMMUNITIES
    )
    # 建图末尾跑一遍实体消解 / 规范别名合并（EXECPLAN2 I-1-4）：把 'OpenAI'/'OpenAI 公司'/
    # '@OpenAI' 等同实体的分裂节点合并到 actors.json 的规范名上。
    # GRAPH-4 / GRAPH-1：默认开——廉价的 embedding+规范名匹配合并（无 LLM），由 0.88 余弦 + 规范名
    # 双重门把关；既是 GRAPH_BUILD_CONCURRENCY 并行建图的 dedup 安全网，又修复碎片化中心度。
    GRAPH_RESOLVE_ENTITIES = os.environ.get('GRAPH_RESOLVE_ENTITIES', 'true').strip().lower() == 'true'
    # 合并所需的最小 embedding 余弦相似度（规范名匹配 + 此阈值 双重门，降低误合并）。
    GRAPH_RESOLVE_SIM_THRESHOLD = float(os.environ.get('GRAPH_RESOLVE_SIM_THRESHOLD', '0.88') or '0.88')
    # 实体消解的 O(N²) 全配对扫描（plan_merges）在节点数很大时会成为纯 Python CPU 墙（实测
    # 大图谱在此步 100% CPU 卡死）。超过此阈值时改走 O(N) 的「精确规范名/别名」快路（plan_merges_fast，
    # 只合并完全同名/同别名的重复节点，跳过昂贵的包含+余弦细化）——仍捕获最高价值的精确重复，绝不卡死。
    # 小图谱（≤ 阈值）仍走完整 plan_merges，与现状逐字节一致。
    GRAPH_RESOLVE_MAX_NODES = int(os.environ.get('GRAPH_RESOLVE_MAX_NODES', '1200') or '1200')
    # 弱连通分量数超过 ratio×节点数时告警（图谱多为孤立单点 = 实体欠合并的特征，KG cookbook 第4步）。
    # KG-8：默认 0.5→0.2。graph_builder 现已按浮点比较（>1 下限保留），0.2 让审计里
    # 132 节点/30 分量的建图真正触发欠合并告警（0.5 时静默放行）。
    GRAPH_COMPONENT_WARN_RATIO = float(os.environ.get('GRAPH_COMPONENT_WARN_RATIO', '0.2') or '0.2')
    # 远程 Graphiti/Zep 才需要分批限流停顿；本地 FalkorDB 关闭死延迟（T2.6）
    GRAPHITI_REMOTE = os.environ.get('GRAPHITI_REMOTE', 'false').strip().lower() == 'true'
    # 每个 Graphiti 操作（add_episode/search/list…）的挂钟上限（秒）。sync→async 桥兜底，
    # 避免某次 LLM/DB 调用卡死时永久阻塞调用它的 Flask 线程。0=不设上限（旧行为）。
    # GRAPH-9：1800→900。fast-tier 路由 + ~5x 更少 episode 让单批远低于 15 分钟；降到 900 让卡死的
    # 读取快速失败而不是干等 30 分钟。
    GRAPHITI_OP_TIMEOUT_S = float(os.environ.get('GRAPHITI_OP_TIMEOUT_S', '900') or '900')

    # --- 模拟（Phase 3）---
    # 智能体数量上限；超过则按 (是否匹配 actor, 影响力, 邻边数) 排序保留，始终保留研究 actor（T3.13）
    OASIS_MAX_AGENTS = int(os.environ.get('OASIS_MAX_AGENTS', '80'))
    # 主角色阵容硬上限（actor-cast discipline）：任何真实预测模拟都应蒸馏到 ≤20 个主角色——
    # 只保留其决策/行动会因果性影响预测结果的 main actors。该上限贯穿全管线：研究抽取
    # （deerflow bridge 按影响力/tier 排序截断 actors.json）、本体生成提示词、以及模拟
    # agent 池（entity 池按主阵容派生，不再向 OASIS_MAX_AGENTS 填充图谱通用节点）。
    # 同时是最大成本杠杆：80→20 个 persona 把画像 LLM 调用与每轮模拟调用都砍 4 倍。
    # 设为 ≥ OASIS_MAX_AGENTS（unset-high）可恢复旧的「填充到 80」行为（degrade-safe）。
    # Hard cap on MAIN actors kept through the pipeline; media/observers are context, not cast.
    ACTOR_CAST_MAX = int(os.environ.get('ACTOR_CAST_MAX', '20') or '20')
    # 媒体/观察者降级：媒体机构、记者、评论员、分析师、智库等「报道/评论方」不是能动 actor
    # ——它们是 context（信息源，tier 3），不应占据主阵容席位或被实例化为模拟 agent。
    # true（默认）时：type=Media / 媒体类 role 的 actor 推断为 simulation_tier=3（显式
    # simulation_tier/archetype 标注仍优先），从而被 agent 准入门（tier 1/2）挡在池外。
    # false 恢复旧行为（media 默认 tier 1 全放行）。Media orgs are demoted to context, never agents.
    ACTOR_EXCLUDE_MEDIA = os.environ.get('ACTOR_EXCLUDE_MEDIA', 'true').strip().lower() == 'true'
    # 主阵容之外的「填充/受众」persona 数（程序化生成的沉默大多数，无逐个 LLM 调研成本）。
    # 默认 0 = agent 池只含主阵容（此前池子会被图谱通用节点填充到 ~80）。兼容旧名
    # SIM_AUDIENCE_SIZE（I-2-2）。Number of filler/audience personas beyond the main cast.
    SIM_AUDIENCE_AGENTS = int(
        os.environ.get('SIM_AUDIENCE_AGENTS', os.environ.get('SIM_AUDIENCE_SIZE', '0') or '0') or '0'
    )
    # 人设活动节律/时区预设：选择 simulation_config_generator 用哪套作息-时区模板。
    # 默认 'china_social' 完整保留当前的北京作息行为（与现状逐字节一致）；
    # 'us_business' / 'global_market' 是另两套预设，由 simulation_config_generator 消费。
    SIM_ACTIVITY_PROFILE = os.environ.get('SIM_ACTIVITY_PROFILE', 'china_social').strip().lower()
    # 模拟 → 图谱反馈回路（本地默认开；写回模拟期间涌现的关系，报告阶段可见）(T3.10)
    SIM_GRAPH_FEEDBACK = os.environ.get('SIM_GRAPH_FEEDBACK', 'true').strip().lower() == 'true'
    # 反馈除自由文本 episode 外，再写带名实体的 typed 边（A LIKED/REPLIED_TO/FOLLOWED B）(T3.10)
    SIM_TYPED_FEEDBACK_EDGES = os.environ.get('SIM_TYPED_FEEDBACK_EDGES', 'true').strip().lower() == 'true'
    # 把 *_config 的 recsys 旋钮（recsys_type/refresh_rec_post_count/max_rec_post_len + echo→
    # following_post_count）映射到 oasis Platform；默认关 = 用 DefaultPlatformType（与旧行为一致）(T3.12)
    SIM_WIRE_RECSYS = os.environ.get('SIM_WIRE_RECSYS', 'false').strip().lower() == 'true'
    # 逐智能体动态情感状态（情绪/精力/立场强度/疲劳）按轮更新并注入该轮提示（EXECPLAN2 I-2-1）。
    # NEXTSTEPS P0-4：默认开——无 intra-agent 状态时多轮模拟只是"N 次独立单轮投票重复 T 次"，
    # 缺少升级/从众/疲劳这些让 discourse 真正移动结果的级联动态。关闭则回到每轮静态人设。
    SIM_AGENT_DYNAMICS = os.environ.get('SIM_AGENT_DYNAMICS', 'true').strip().lower() == 'true'
    # 种子内容兜底：事件配置 LLM 有时返回 0 条 initial_posts。此时智能体开局面对空 feed，
    # 只能 FOLLOW（无内容可评论/转发）→ 整场模拟 0 条有机帖/评论（"空转/hollow"，历史上几乎
    # 每次运行的根因）。开启后，当且仅当 LLM 未产出初始帖时，从研究档案合成种子帖（按影响力/
    # tier 取头部角色，各自就一个热点议题陈述其实证立场），让开局 feed 承载真实、对立的观点。
    # 窄触发（只补空缺失的失败态，不改 LLM 已产出帖的正常路径）+ degrade-safe（异常→空，与现状一致）。
    SIM_SYNTH_SEED_POSTS = os.environ.get('SIM_SYNTH_SEED_POSTS', 'true').strip().lower() == 'true'
    SIM_SYNTH_SEED_POSTS_MAX = int(os.environ.get('SIM_SYNTH_SEED_POSTS_MAX', '10') or '10')
    # 世界简报注入：把「预测问题 + 局势简报 + 热点话题」拼成 ≤1400 字的共同世界背景，写入模拟配置
    # world_brief 字段，运行时追加到每个 agent 的 system prompt——agent 知道这个世界在争论什么。
    SIM_WORLD_BRIEF = os.environ.get('SIM_WORLD_BRIEF', 'true').strip().lower() == 'true'
    # 人格设计（实证语境工程）：对有档案的真实主体（政府/企业/机构），在画像 LLM 调用中要求产出
    # 结构化 persona_design（identity/views_beliefs/incentives/objectives/relations/red_lines/
    # decision_style/rhetoric），严格接地于研究档案、禁止发明立场；多样性来自真实主体的真实差异。
    SIM_PERSONA_DESIGN = os.environ.get('SIM_PERSONA_DESIGN', 'true').strip().lower() == 'true'
    # ITEM 11 市场先验注入（SIM_MARKET_PRIORS）：把本次运行 handoff/prediction_markets.json 里
    # relevance-gated 的市场隐含概率（Yes 价）灌进模拟侧——(1) 世界底稿 world_brief 追加一段
    # 「市场定价」块（top 5 相关市场：question + 隐含 P），让全体 Agent 知道priced beliefs 并据此
    # 争论；(2) 对「分析师/媒体」类人设追加一行市场感知提示（其关注话题与市场问题词重叠时）。
    # 文件缺失/解析失败 / 无对应 handoff → 与今日行为逐字节一致（degrade-safe）。默认开。
    SIM_MARKET_PRIORS = os.environ.get('SIM_MARKET_PRIORS', 'true').strip().lower() == 'true'
    # Polymarket 预测市场（官方公开 Gamma API，keyless）：研究阶段拉取与题目相关的活跃市场，
    # 把市场隐含概率（Yes 价）作为校准锚注入研究报告 + 报告章节 + 二元预测抽取（预测 vs 市场
    # 分歧 >10pt 须解释）。无需 API key；网络失败 → 静默跳过（degrade-safe），不影响主流程。
    PREDICTION_MARKETS_ENABLED = os.environ.get('PREDICTION_MARKETS_ENABLED', 'true').strip().lower() == 'true'
    # 市场快照上限（按成交量降序取头部）。新名 PREDICTION_MARKETS_MAX；旧名 ODDPOOL_MAX_MARKETS 仍读作兜底以平滑迁移。
    PREDICTION_MARKETS_MAX = int(os.environ.get('PREDICTION_MARKETS_MAX',
                                                os.environ.get('ODDPOOL_MAX_MARKETS', '20')) or '20')
    # 成交量噪声下限（USDC）：低于此量的市场价格是噪声，不配当锚点。
    PREDICTION_MARKETS_MIN_VOLUME = float(os.environ.get('PREDICTION_MARKETS_MIN_VOLUME', '200') or '200')
    # 每个事件最多取几条市场：防止一个多结局事件的子市场阶梯（如「N 次降息」0~12）霸占全部名额，
    # 保证快照的事件多样性。<=0 视为不限制。
    PREDICTION_MARKETS_MAX_PER_EVENT = int(os.environ.get('PREDICTION_MARKETS_MAX_PER_EVENT', '3') or '3')
    # 每个检索词最多取几个匹配事件（Gamma public-search 的 limit_per_type，8→15）：检索词已被
    # LLM/启发式收敛到题目相关，放宽召回不放噪声——快照仍受 MAX/MIN_VOLUME/MAX_PER_EVENT 收口。
    PREDICTION_MARKETS_PER_QUERY = int(os.environ.get('PREDICTION_MARKETS_PER_QUERY', '15') or '15')
    # 相关性门槛（0~10）：调用方提供 llm_call 时对快照批量打相关性分，低于此分的市场剔除、
    # 其余按 (relevance_score, volume) 重排——错误锚点比没有锚点更糟。未打分/打分失败 →
    # 保持 volume 排序（与今天一致，degrade-safe）。
    PREDICTION_MARKETS_MIN_RELEVANCE = float(os.environ.get('PREDICTION_MARKETS_MIN_RELEVANCE', '5') or '5')
    # PM-3：报告阶段对研究期落盘的市场快照做实时重报价（handoff-PLUS-refresh）——保留研究期价
    # price_at_research 与当下价，算 Δ 呈现「研究→现在」的价格移动，并在二元预测抽取前再重报价一次
    # 使 market_anchor 用现价。关闭 → 只用研究期快照（在包头标注时效性）。重报价失败一律 degrade-safe：
    # 保留研究期价 + 时效性说明，绝不阻断报告（PolymarketClient.requote_markets 每行自带失败标记）。
    PREDICTION_MARKETS_REQUOTE = os.environ.get('PREDICTION_MARKETS_REQUOTE', 'true').strip().lower() == 'true'
    # B2 三部结构：按 Bridgewater 简报把成稿组织为 Part 1（二元预测表）/ Part 2（框架综合，
    # 单次 LLM 调用、字数按 requirement_spec 的 page_budget 收敛）/ Part 3（附录 = 原详细章节）。
    # 无 Part 1 或综合产出过短 → 跳过不改文档（degrade-safe，幂等）。
    REPORT_THREE_PART_SKELETON = os.environ.get('REPORT_THREE_PART_SKELETON', 'true').strip().lower() == 'true'
    # 情感状态更新的学习率/速率常数（有界 clamp，保守取值；仅 SIM_AGENT_DYNAMICS=true 时生效）。
    SIM_DYNAMICS_MOOD_LR = float(os.environ.get('SIM_DYNAMICS_MOOD_LR', '0.25') or '0.25')
    SIM_DYNAMICS_OPINION_LR = float(os.environ.get('SIM_DYNAMICS_OPINION_LR', '0.15') or '0.15')
    SIM_DYNAMICS_FATIGUE_RATE = float(os.environ.get('SIM_DYNAMICS_FATIGUE_RATE', '0.20') or '0.20')
    SIM_DYNAMICS_FATIGUE_DECAY = float(os.environ.get('SIM_DYNAMICS_FATIGUE_DECAY', '0.10') or '0.10')
    # NEXTSTEPS P1-1（决策/承诺通道 + 演化 WorldState）：让 agent 不只发帖，而是每轮就 forecast
    # 情景做一次结构化"承诺"，按资源/影响力加权演化一个由 base_rates 初始化的**结果世界态**，把
    # 模拟从"度量声量"变为"度量结果"。默认关（每个活跃 agent 每轮多一次结构化 LLM 调用，成本可观；
    # 关闭则与现状逐字节一致：只有声量份额 final_stance_share）。落 decisions.jsonl + world_state_trajectory.json。
    # R2-SIM-1 / R2-CAL-3：默认开——硬前提：没有它脊柱只看到活动量、零建模结果。成本由
    # OASIS_DEFAULT_MAX_ROUNDS 封顶 + SIM_CONVERGENCE_STOP 早停 + 并行 elicitation 约束。
    SIM_DECISION_CHANNEL = os.environ.get('SIM_DECISION_CHANNEL', 'true').strip().lower() == 'true'
    SIM_DECISION_INERTIA = float(os.environ.get('SIM_DECISION_INERTIA', '0.7') or '0.7')  # 先验每轮持久度
    # NEXTSTEPS P1-4：收敛/均衡检测——按 WorldState 的逐轮变化 EWMA 早停（区别于按声量），
    # 把"收敛于 R 轮（稳定）" vs "未收敛（低信心）"本身作为校准信号。需 P1-1 的世界态（现已默认开）。
    # SIM-1：默认开——观点动力学通常 10-25 轮settle，72→~20 是 ~3-4x 更少 sim 调用；收敛-at-R 成为校准信号。
    SIM_CONVERGENCE_STOP = os.environ.get('SIM_CONVERGENCE_STOP', 'true').strip().lower() == 'true'
    SIM_CONVERGENCE_EPS = float(os.environ.get('SIM_CONVERGENCE_EPS', '0.02') or '0.02')
    SIM_CONVERGENCE_WINDOW = int(os.environ.get('SIM_CONVERGENCE_WINDOW', '3') or '3')
    # 模拟中断后从上次完成的轮次继续（而非从第 0 轮重启），依赖 OASIS DB 持久性（EXECPLAN2 I-4-2）。
    # 默认关 = 全量重启（与现状一致）。
    SIM_RESUME_FROM_ROUND = os.environ.get('SIM_RESUME_FROM_ROUND', 'false').strip().lower() == 'true'

    # —— 本体契约：实体分类 / 显著度排序 / valenced 关系（CLAUDE/CODEX/GEMINI 三方收敛）——
    # 智能体池按 is_agent_eligible() 收敛：仅 archetype actor/collective（simulation_tier 1/2）入池，
    # 记者/媒体/抽象概念（tier 3/4）不再被当作仿真智能体。默认开；当没有实体携带 archetype/
    # simulation_tier 时自动 no-op（保留全部，与现状一致）。
    SIM_TIER_ELIGIBILITY = os.environ.get('SIM_TIER_ELIGIBILITY', 'True').strip().lower() == 'true'
    # 智能体上限裁剪改按 salience_score() 排序（而非纯邻边度数）。默认开；salience 缺失时回退到
    # 现状的 (是否匹配 actor, 影响力, 邻边数) 排序元组（与现状一致）。
    SIM_SALIENCE_RANKING = os.environ.get('SIM_SALIENCE_RANKING', 'True').strip().lower() == 'true'
    # 关注图 + 情感用关系 valence/polarity（盟友≠对手≠交易方）。默认开；仅对新增关系类型生效，
    # 既有 8 类 legacy 关系行为与现状逐字节一致。
    SIM_VALENCED_RELATIONS = os.environ.get('SIM_VALENCED_RELATIONS', 'True').strip().lower() == 'true'

    # --- 报告（Phase 4）---
    # 每节最少/对话模式最多工具调用（与 REPORT_AGENT_MAX_TOOL_CALLS 配套；T4.4 接入硬编码值）
    REPORT_AGENT_MIN_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MIN_TOOL_CALLS', '4'))
    REPORT_AGENT_MAX_TOOL_CALLS_CHAT = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS_CHAT', '2'))
    # 用原生 tool calling 取代脆弱的 regex ReAct（T4.5）。
    # REPORT-4：默认开——消除 conflict/contamination 的纠正往返与 contamination-adoption 失败模式；
    # 每章遇异常自动 per-section 回退到 ReAct（degrade-safe）。
    REPORT_NATIVE_TOOLS = os.environ.get('REPORT_NATIVE_TOOLS', 'true').strip().lower() == 'true'
    # 并发生成报告章节（EXECPLAN2 I-6-3）：>1 时正文章节走线程池并行，摘要/结论章节最后串行
    # （依赖正文全文）。章节级 LLM 并发受 OASIS 信号量同源约束。
    # REPORT-1：1→3。正文章节相互独立，并行 ~2.5-3.5x 加速；正文段自动走 brief 上下文避免 O(N²) token。
    # PAR-3：3→6——正文章节彼此独立、并发受 OASIS 信号量同源约束封顶，进一步抬高并发上限压缩
    # 报告阶段 wall-clock；实际并发始终 min(此值, 正文章节数)，故对短报告无副作用。
    REPORT_SECTION_CONCURRENCY = int(os.environ.get('REPORT_SECTION_CONCURRENCY', '6') or '6')
    # 章节上下文模式（I-6-3）：full = 每章注入此前所有章节全文；brief = 注入大纲+各章 1-2 句摘要
    # （去除 O(N²) 上下文膨胀）。并发模式下正文章节强制用 brief（并行时拿不到彼此全文）。
    # REPORT-2：默认 brief——大致砍半报告输入 token；尾/摘要章节仍拿全文，执行摘要不受影响。
    REPORT_SECTION_CONTEXT_MODE = os.environ.get('REPORT_SECTION_CONTEXT_MODE', 'brief').strip().lower()

    # --- DeerFlow 模型 / Key / 预算 单一真源（T6.4 / T6.6）---
    # antigravity = vibeproxy 本地 OpenAI 兼容代理（config.yaml 的 antigravity stanza，
    # 用占位 key sk-dummy，无需环境变量 Key，故不入 DEERFLOW_KEY_ENV）。
    SUPPORTED_DEERFLOW_MODELS = ('claude', 'codex', 'minimax', 'deepseek', 'qwen', 'glm', 'kimi', 'antigravity')
    # 模型 → 所需 Key 环境变量（claude/codex 用本机订阅，无需 Key；antigravity 用本地代理占位 key）
    DEERFLOW_KEY_ENV = {
        'minimax': 'MINIMAX_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY',
        'qwen': 'DASHSCOPE_API_KEY', 'glm': 'ZHIPUAI_API_KEY', 'kimi': 'KIMI_API_KEY',
    }
    # 研究深度 → 超时预算（秒）；DEERFLOW_RESEARCH_TIMEOUT 为显式覆盖（优先级最高）(T6.6)
    # CONF-1：standard 2400→7200、deep 10800→21600——旧预算下 deep 的多轮协议经常被看门狗
    # 无差别 SIGKILL 在综合阶段之前。原始 dict 保留为兼容契约；内部读者应走 deerflow_depth_budget()。
    DEERFLOW_DEPTH_BUDGETS = {'quick': 900, 'standard': 7200, 'deep': 21600}
    # deep 开场 pass 的递归上限（旧版在 bridge 内直接读 os.environ；提升为 Config 属性）(T6.6)
    # CONF-1：220→400——扇出/双轨默认开后开场 pass 的工具调用数近乎翻倍，220 会提前截断 scope。
    DEERFLOW_DEEP_OPENING_RECURSION_LIMIT = int(os.environ.get('DEERFLOW_DEEP_OPENING_RECURSION_LIMIT', '400'))

    @classmethod
    def deerflow_depth_budget(cls, depth) -> int:
        """研究深度 → 生效超时预算（秒），带并行模式自动放大（CONF-1）。

        DEERFLOW_DUAL_TRACK / DEERFLOW_SUBAGENTS / RESEARCH_DEEP_FANOUT 任一开启时，
        研究阶段的工作量近乎翻倍（双轨两套工作流 / 子代理扇出），固定档位预算会把
        更深的跑法无差别 SIGKILL——故生效预算 ×1.5。原始 DEERFLOW_DEPTH_BUDGETS dict
        保持不变（兼容契约）；显式 timeout 参数 / 用户 .env 里的 DEERFLOW_RESEARCH_TIMEOUT
        仍由调用方优先（本方法只负责档位默认值）。degrade-safe：任何异常回退到未放大的
        原始档位值。
        """
        raw = cls.DEERFLOW_DEPTH_BUDGETS.get(
            str(depth or '').strip().lower(), cls.DEERFLOW_RESEARCH_TIMEOUT)
        try:
            if (getattr(cls, 'DEERFLOW_DUAL_TRACK', False)
                    or getattr(cls, 'DEERFLOW_SUBAGENTS', False)
                    or getattr(cls, 'RESEARCH_DEEP_FANOUT', False)):
                return int(raw * 1.5)
            return int(raw)
        except (TypeError, ValueError):  # noqa: BLE001 — 预算计算绝不阻塞研究启动
            return raw

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

# GRAPH-3：runtime.py 在 services/graphiti_client/runtime.py 里直接 os.environ.get(
# "GRAPHITI_MAX_COROUTINES", "8") 读取该旋钮（绕过 Config 类属性）。把上面 Config 解析出的默认值
# 下发到进程环境（仅在 .env / 环境未显式设置时填充），让 16 的文档化默认真正在 runtime 生效，
# 同时尊重用户显式覆盖（setdefault 不会覆盖既有值）。
os.environ.setdefault('GRAPHITI_MAX_COROUTINES', str(Config.GRAPHITI_MAX_COROUTINES))
