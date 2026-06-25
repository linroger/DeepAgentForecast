"""
OASIS 双平台并行模拟预设脚本
同时运行Twitter和Reddit模拟，读取相同的配置文件

功能特性:
- 双平台（Twitter + Reddit）并行模拟
- 完成模拟后不立即关闭环境，进入等待命令模式
- 支持通过IPC接收Interview命令
- 支持单个Agent采访和批量采访
- 支持远程关闭环境命令

使用方式:
    python run_parallel_simulation.py --config simulation_config.json
    python run_parallel_simulation.py --config simulation_config.json --no-wait  # 完成后立即关闭
    python run_parallel_simulation.py --config simulation_config.json --twitter-only
    python run_parallel_simulation.py --config simulation_config.json --reddit-only

日志结构:
    sim_xxx/
    ├── twitter/
    │   └── actions.jsonl    # Twitter 平台动作日志
    ├── reddit/
    │   └── actions.jsonl    # Reddit 平台动作日志
    ├── simulation.log       # 主模拟进程日志
    └── run_state.json       # 运行状态（API 查询用）
"""

# ============================================================
# 解决 Windows 编码问题：在所有 import 之前设置 UTF-8 编码
# 这是为了修复 OASIS 第三方库读取文件时未指定编码的问题
# ============================================================
import sys
import os

if sys.platform == 'win32':
    # 设置 Python 默认 I/O 编码为 UTF-8
    # 这会影响所有未指定编码的 open() 调用
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    
    # 重新配置标准输出流为 UTF-8（解决控制台中文乱码）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    
    # 强制设置默认编码（影响 open() 函数的默认编码）
    # 注意：这需要在 Python 启动时就设置，运行时设置可能不生效
    # 所以我们还需要 monkey-patch 内置的 open 函数
    import builtins
    _original_open = builtins.open
    
    def _utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None, 
                   newline=None, closefd=True, opener=None):
        """
        包装 open() 函数，对于文本模式默认使用 UTF-8 编码
        这可以修复第三方库（如 OASIS）读取文件时未指定编码的问题
        """
        # 只对文本模式（非二进制）且未指定编码的情况设置默认编码
        if encoding is None and 'b' not in mode:
            encoding = 'utf-8'
        return _original_open(file, mode, buffering, encoding, errors, 
                              newline, closefd, opener)
    
    builtins.open = _utf8_open

import argparse
import asyncio
import json
import logging
import multiprocessing
import random
import signal
import sqlite3
import warnings
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


# 全局变量：用于信号处理
_shutdown_event = None
_cleanup_done = False


# EXECPLAN2 I-7-2: 确定性随机数种子（默认 None = 维持当前非确定性行为）。
# 设置环境变量 SIM_SEED=<int> 后，调度采样（加权水库 / 概率发帖）变为可复现，
# 使 prepare/run 阶段的快照/黄金测试与 A/B 评估成为可能。未设置时 random.Random()
# 从系统熵播种，与历史上的 random.random() 行为逐次一致（零行为变化）。
def _build_sampling_rng() -> "random.Random":
    """根据 SIM_SEED 构造本进程的采样 RNG（缺省即非确定性，与旧行为一致）。"""
    raw = os.environ.get("SIM_SEED")
    if raw is None or str(raw).strip() == "":
        return random.Random()
    try:
        return random.Random(int(str(raw).strip()))
    except (TypeError, ValueError):
        # 容错：非整数种子退回非确定性，避免因配置错误中断模拟。
        return random.Random()


_RNG = _build_sampling_rng()

# 添加 backend 目录到路径
# 脚本固定位于 backend/scripts/ 目录
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# 加载项目根目录的 .env 文件（包含 LLM_API_KEY 等配置）
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
    print(f"已加载环境配置: {_env_file}")
else:
    # 尝试加载 backend/.env
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
        print(f"已加载环境配置: {_backend_env}")


class MaxTokensWarningFilter(logging.Filter):
    """过滤掉 camel-ai 关于 max_tokens 的警告（我们故意不设置 max_tokens，让模型自行决定）"""
    
    def filter(self, record):
        # 过滤掉包含 max_tokens 警告的日志
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# 在模块加载时立即添加过滤器，确保在 camel 代码执行前生效
logging.getLogger().addFilter(MaxTokensWarningFilter())


def disable_oasis_logging():
    """
    禁用 OASIS 库的详细日志输出
    OASIS 的日志太冗余（记录每个 agent 的观察和动作），我们使用自己的 action_logger
    """
    # 禁用 OASIS 的所有日志器
    oasis_loggers = [
        "social.agent",
        "social.twitter", 
        "social.rec",
        "oasis.env",
        "table",
    ]
    
    for logger_name in oasis_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)  # 只记录严重错误
        logger.handlers.clear()
        logger.propagate = False


def init_logging_for_simulation(simulation_dir: str):
    """
    初始化模拟的日志配置
    
    Args:
        simulation_dir: 模拟目录路径
    """
    # 禁用 OASIS 的详细日志
    disable_oasis_logging()
    
    # 清理旧的 log 目录（如果存在）
    old_log_dir = os.path.join(simulation_dir, "log")
    if os.path.exists(old_log_dir):
        import shutil
        shutil.rmtree(old_log_dir, ignore_errors=True)


from action_logger import SimulationLogManager, PlatformActionLogger
from app.utils.oasis_llm import create_oasis_model, get_oasis_semaphore

try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph,
        generate_reddit_agent_graph
    )
except ImportError as e:
    print(f"错误: 缺少依赖 {e}")
    print("请先安装: pip install oasis-ai camel-ai")
    sys.exit(1)


# Twitter可用动作（不包含INTERVIEW，INTERVIEW只能通过ManualAction手动触发）
# T3.11: 加入 CREATE_COMMENT（最大结构性收益——让推文形成回复线程，而非只有转发/引用），
# 以及 SEARCH_POSTS / TREND。三者在 camel-oasis 0.2.5 platform.py 均有平台无关的处理器
# （create_comment@1079 / search_posts@773 / trend@1030），对 Twitter 同样可用。
TWITTER_ACTIONS = [
    ActionType.CREATE_POST,
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.FOLLOW,
    ActionType.DO_NOTHING,
    ActionType.QUOTE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.SEARCH_POSTS,
    ActionType.TREND,
]

# Reddit可用动作（不包含INTERVIEW，INTERVIEW只能通过ManualAction手动触发）
REDDIT_ACTIONS = [
    ActionType.LIKE_POST,
    ActionType.DISLIKE_POST,
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_COMMENT,
    ActionType.DISLIKE_COMMENT,
    ActionType.SEARCH_POSTS,
    ActionType.SEARCH_USER,
    ActionType.TREND,
    ActionType.REFRESH,
    ActionType.DO_NOTHING,
    ActionType.FOLLOW,
    ActionType.MUTE,
]


# ============================================================
# EXECPLAN2 I-2-3: 按角色定制的「动作可供性」（per-role action affordances）
# ------------------------------------------------------------
# 现状：同一平台的所有 Agent 共用一份全局动作清单（TWITTER_ACTIONS / REDDIT_ACTIONS），
# 政府账号、官媒、学生、匿名水军拥有完全相同的可选动作（都能 REPOST/QUOTE/FOLLOW/MUTE…）。
# 现实中可供性与行为习惯是角色相关的：官方账号很少给个人点赞/转发/拉黑，媒体大量引用/造势，
# 活动家激进转发/关注，潜水受众多为点赞/沉默。人设文本会暗示这点，但没有任何东西约束动作空间，
# 于是 LLM 频繁选出「跳戏」的动作，稀释保真度。
#
# 方案：以 entity_type（角色）为键，为每个 Agent 量身裁剪 available_actions。camel-oasis 在
# 构图时已把动作转成 ChatAgent 的工具（SocialAgent.__init__ 把 available_actions 过滤成
# action_tools 并注入 ChatAgent.tools）；ChatAgent 暴露了公开的 remove_tools(name) API，
# 工具名恰为 ActionType.value。因此「构图后按角色裁剪」是库原生支持的——在 generate_*_agent_graph
# 之后、env.reset() 之前，逐个 Agent 移除不属于其角色策略的社交动作工具即可（仅移除社交动作，
# 不动 INTERVIEW 等其它工具）。同时把一句简短的「行为习惯」约束注入 system prompt，让 LLM 自我设限。
#
# 可降级不变式：默认 SIM_ROLE_ACTION_PROFILES!=true → 完全跳过，沿用单一全局清单（逐字节旧行为）。
# 任意环节失败（缺 entity_type、库 API 变动、工具名不匹配）一律 best-effort 跳过该 Agent，绝不中断模拟。
#
# 策略表的「白名单」会与平台 union 清单求交，确保永远不会授予平台不存在/未启用的动作；
# 任何角色都至少保留 CREATE_POST / CREATE_COMMENT / DO_NOTHING，避免把某类角色变成完全惰性。
# ============================================================

# 每个角色策略只在 union 动作集合内做「白名单」过滤；未列出的 entity_type 走 _ROLE_ACTION_DEFAULT。
# 注：键为 entity_type.lower()，与 oasis_profile_generator 的 INDIVIDUAL/GROUP_ENTITY_TYPES 命名一致。
ROLE_ACTION_POLICY = {
    # —— 机构 / 群体 ——
    # 政府机构：几乎只发布官方声明、检索舆情；不给个人点赞/转发/拉黑。
    "governmentagency": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "official": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    # 媒体：大量发帖/引用/转发/造势/评论，构成信息放大主力。
    "mediaoutlet": [
        ActionType.CREATE_POST,
        ActionType.QUOTE_POST,
        ActionType.REPOST,
        ActionType.CREATE_COMMENT,
        ActionType.TREND,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "journalist": [
        ActionType.CREATE_POST,
        ActionType.QUOTE_POST,
        ActionType.REPOST,
        ActionType.CREATE_COMMENT,
        ActionType.SEARCH_POSTS,
        ActionType.SEARCH_USER,
        ActionType.DO_NOTHING,
    ],
    # 高校 / NGO / 公司 / 机构：偏官方口径，少量互动。
    "university": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "ngo": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.QUOTE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "company": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "organization": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "institution": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "group": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.LIKE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    "community": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.LIKE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    # —— 个人 ——
    # 专家 / 教授：发表观点、评论、引用佐证，少量转发。
    "professor": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.QUOTE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.LIKE_POST,
        ActionType.DO_NOTHING,
    ],
    "expert": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.QUOTE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.LIKE_POST,
        ActionType.DO_NOTHING,
    ],
    "faculty": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.QUOTE_POST,
        ActionType.SEARCH_POSTS,
        ActionType.LIKE_POST,
        ActionType.DO_NOTHING,
    ],
    "publicfigure": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.QUOTE_POST,
        ActionType.REPOST,
        ActionType.LIKE_POST,
        ActionType.FOLLOW,
        ActionType.DO_NOTHING,
    ],
    # 活动家：激进——大量转发/关注/评论/造势以扩散立场。
    "activist": [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.REPOST,
        ActionType.QUOTE_POST,
        ActionType.LIKE_POST,
        ActionType.FOLLOW,
        ActionType.TREND,
        ActionType.SEARCH_POSTS,
        ActionType.DO_NOTHING,
    ],
    # 学生 / 普通个人：完整社交动作集（最自由）——见 _ROLE_FULL_ACCESS，直接放行平台全部 union。
    # —— 程序化「沉默的大多数」受众（simulation_config_generator.AUDIENCE_ENTITY_TYPE="Audience"）——
    # 以点赞 / 偶尔评论 / 转发 / 大量沉默为主，几乎不发起原创帖、不关注、不拉黑。
    "audience": [
        ActionType.LIKE_POST,
        ActionType.DISLIKE_POST,
        ActionType.LIKE_COMMENT,
        ActionType.REPOST,
        ActionType.CREATE_COMMENT,
        ActionType.DO_NOTHING,
        ActionType.REFRESH,
    ],
}

# 完整动作集角色：最自由的个人账号，直接放行平台全部 union（不做任何裁剪）。
# 等价于设计草图里 'student': TWITTER_ACTIONS 的「完整社交动作集」语义，但与平台无关——
# Reddit 上同样拿到 REDDIT_ACTIONS 全集，而非被 Twitter 清单截断。
_ROLE_FULL_ACCESS = {"student", "alumni", "person"}

# 未匹配到具体角色时的兜底策略：保守地保留发帖/评论/检索/沉默/点赞，避免越权放大类动作。
_ROLE_ACTION_DEFAULT = [
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_POST,
    ActionType.SEARCH_POSTS,
    ActionType.DO_NOTHING,
]

# 不变式：任何角色都至少保留这几样基本动作，防止过度限制把某类 Agent 变成完全惰性。
_ROLE_ACTION_FLOOR = {
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.DO_NOTHING,
}

# 角色 → 注入 system prompt 的「行为习惯」一句话约束（让 LLM 在工具裁剪之外再自我设限）。
# 仅覆盖最容易跳戏的机构/媒体/活动家/受众；未列出的角色不注入额外约束（保持原 persona）。
_ROLE_BEHAVIOR_HINT = {
    "governmentagency": "【行为习惯】你是官方机构账号：通常只发布正式声明或检索舆情，几乎从不给个人点赞、转发或拉黑。",
    "official": "【行为习惯】你是官方机构账号：通常只发布正式声明或检索舆情，几乎从不给个人点赞、转发或拉黑。",
    "mediaoutlet": "【行为习惯】你是媒体账号：以发布报道、引用与转发关键信息、推动话题热度为主，很少给普通用户点赞。",
    "journalist": "【行为习惯】你是记者：以发布报道、引用与求证、关注信源为主，较少随手点赞。",
    "activist": "【行为习惯】你是活动家：积极转发、关注同立场账号、评论与造势以扩散你的主张。",
    "audience": "【行为习惯】你是普通围观受众（沉默的大多数）：多数时候只是点赞或沉默，偶尔评论或转发，几乎不发起原创长帖。",
}


def _build_role_type_map(config: Dict[str, Any]) -> Dict[int, str]:
    """EXECPLAN2 I-2-3: 从 simulation_config 构建 agent_id -> entity_type（小写）映射。

    与 get_agent_names_from_config 同源（config["agent_configs"]，每项含 agent_id/entity_type），
    缺字段则跳过该项。供按角色裁剪动作可供性使用。
    """
    role_map: Dict[int, str] = {}
    for agent_config in config.get("agent_configs", []) or []:
        agent_id = agent_config.get("agent_id")
        entity_type = agent_config.get("entity_type")
        if agent_id is not None and entity_type:
            role_map[agent_id] = str(entity_type).strip().lower()
    return role_map


def _allowed_actions_for_role(entity_type: str, platform_union: List["ActionType"]) -> List["ActionType"]:
    """EXECPLAN2 I-2-3: 计算某角色在给定平台上的「允许动作」白名单。

    策略表白名单 ∩ 平台 union（绝不授予平台不存在/未启用的动作），再并入基本动作下限
    （_ROLE_ACTION_FLOOR，同样需在 union 内）。返回顺序去重、稳定。
    完整动作集角色（_ROLE_FULL_ACCESS）直接返回平台 union 全集，不做裁剪。
    """
    role = (entity_type or "").lower()
    if role in _ROLE_FULL_ACCESS:
        return list(platform_union)
    policy = ROLE_ACTION_POLICY.get(role, _ROLE_ACTION_DEFAULT)
    union_set = set(platform_union)
    allowed_set = (set(policy) & union_set) | (_ROLE_ACTION_FLOOR & union_set)
    # 以平台 union 的顺序产出，便于阅读 / 日志稳定。
    return [a for a in platform_union if a in allowed_set]


def _apply_role_action_profiles(
    agent_graph,
    config: Dict[str, Any],
    platform_union: List["ActionType"],
    log_info,
) -> None:
    """EXECPLAN2 I-2-3: 在构图后、env.reset() 前，按角色裁剪每个 Agent 的社交动作工具。

    机制：camel ChatAgent 把每个动作注册为名为 ActionType.value 的工具。对每个 Agent，
    移除「属于平台 union 但不在该角色白名单」的社交动作工具（仅社交动作，绝不动 INTERVIEW 等
    其它工具）。同时把一句「行为习惯」约束追加进 system prompt，让 LLM 自我设限。

    可降级：任意 Agent 处理失败仅跳过该 Agent（best-effort），不抛出、不中断模拟。
    仅当 SIM_ROLE_ACTION_PROFILES=true 时由调用方触发；默认完全不进入本函数。
    """
    if agent_graph is None:
        return
    role_map = _build_role_type_map(config)
    # 平台所有社交动作的工具名集合——只在这个集合内做增删，避免误删非社交工具。
    union_tool_names = {a.value for a in platform_union}

    restricted = 0
    hinted = 0
    try:
        agents = agent_graph.get_agents()
    except Exception as e:  # noqa: BLE001
        log_info(f"角色动作裁剪跳过（无法枚举 Agent）: {e}")
        return

    for agent_id, agent in agents:
        try:
            entity_type = role_map.get(agent_id, "")
            allowed = _allowed_actions_for_role(entity_type, platform_union)
            allowed_names = {a.value for a in allowed}
            # 待移除 = 平台社交动作 - 允许动作；只在 agent 实际拥有的工具里删。
            to_remove = [
                name for name in union_tool_names
                if name not in allowed_names and name in getattr(agent, "_internal_tools", {})
            ]
            if to_remove:
                agent.remove_tools(to_remove)
                restricted += 1

            # 软约束：把「行为习惯」一句话注入 system prompt（best-effort，失败不影响硬裁剪）。
            hint = _ROLE_BEHAVIOR_HINT.get((entity_type or "").lower())
            if hint and _inject_behavior_hint(agent, hint):
                hinted += 1
        except Exception as e:  # noqa: BLE001
            log_info(f"角色动作裁剪跳过 Agent {agent_id}（已隔离）: {e}")
            continue

    log_info(f"角色动作可供性已应用: 裁剪 {restricted} 个 Agent，注入行为约束 {hinted} 个")


def _inject_behavior_hint(agent, hint: str) -> bool:
    """EXECPLAN2 I-2-3: 把一句行为约束追加进 Agent 的 system prompt（best-effort）。

    走 camel ChatAgent 受支持的路径：基于 _original_system_message 重建系统消息并
    init_messages() 把它重新写入记忆。任何版本差异/缺属性即返回 False（降级，不影响硬裁剪）。
    在 env.reset() 之前调用——reset()→generate_custom_agents 不会重置记忆，注入得以保留。
    """
    try:
        original = getattr(agent, "_original_system_message", None)
        if original is None or not hasattr(original, "content"):
            return False
        if hint in (original.content or ""):
            return True  # 已注入过，幂等
        new_msg = original.create_new_instance((original.content or "") + "\n\n" + hint)
        agent._original_system_message = new_msg
        # 触发系统消息按输出语言重算并重新写入记忆（与 camel 内部行为一致）。
        agent._system_message = agent._generate_system_message_for_output_language()
        agent.init_messages()
        return True
    except Exception:
        return False


def _role_action_profiles_enabled() -> bool:
    """EXECPLAN2 I-2-3: 特性开关（与 SIM_WIRE_RECSYS / SIM_EMERGENT_METRICS 同风格的环境变量）。

    默认关闭 → 维持单一全局动作清单的旧行为（可降级不变式）。
    """
    return os.environ.get("SIM_ROLE_ACTION_PROFILES", "false").strip().lower() == "true"


# IPC相关常量
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

class CommandType:
    """命令类型常量"""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class ParallelIPCHandler:
    """
    双平台IPC命令处理器
    
    管理两个平台的环境，处理Interview命令
    """
    
    def __init__(
        self,
        simulation_dir: str,
        twitter_env=None,
        twitter_agent_graph=None,
        reddit_env=None,
        reddit_agent_graph=None
    ):
        self.simulation_dir = simulation_dir
        self.twitter_env = twitter_env
        self.twitter_agent_graph = twitter_agent_graph
        self.reddit_env = reddit_env
        self.reddit_agent_graph = reddit_agent_graph
        
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        
        # 确保目录存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def update_status(self, status: str):
        """更新环境状态"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "twitter_available": self.twitter_env is not None,
                "reddit_available": self.reddit_env is not None,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_command(self) -> Optional[Dict[str, Any]]:
        """轮询获取待处理命令"""
        if not os.path.exists(self.commands_dir):
            return None
        
        # 获取命令文件（按时间排序）
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))
        
        command_files.sort(key=lambda x: x[1])
        
        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
        
        return None
    
    def send_response(self, command_id: str, status: str, result: Dict = None, error: str = None):
        """发送响应"""
        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        
        # 删除命令文件
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass
    
    def _get_env_and_graph(self, platform: str):
        """
        获取指定平台的环境和agent_graph
        
        Args:
            platform: 平台名称 ("twitter" 或 "reddit")
            
        Returns:
            (env, agent_graph, platform_name) 或 (None, None, None)
        """
        if platform == "twitter" and self.twitter_env:
            return self.twitter_env, self.twitter_agent_graph, "twitter"
        elif platform == "reddit" and self.reddit_env:
            return self.reddit_env, self.reddit_agent_graph, "reddit"
        else:
            return None, None, None
    
    async def _interview_single_platform(self, agent_id: int, prompt: str, platform: str) -> Dict[str, Any]:
        """
        在单个平台上执行Interview
        
        Returns:
            包含结果的字典，或包含error的字典
        """
        env, agent_graph, actual_platform = self._get_env_and_graph(platform)
        
        if not env or not agent_graph:
            return {"platform": platform, "error": f"{platform}平台不可用"}
        
        try:
            agent = agent_graph.get_agent(agent_id)
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            actions = {agent: interview_action}
            await env.step(actions)
            
            result = self._get_interview_result(agent_id, actual_platform)
            result["platform"] = actual_platform
            return result
            
        except Exception as e:
            return {"platform": platform, "error": str(e)}
    
    async def handle_interview(self, command_id: str, agent_id: int, prompt: str, platform: str = None) -> bool:
        """
        处理单个Agent采访命令
        
        Args:
            command_id: 命令ID
            agent_id: Agent ID
            prompt: 采访问题
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None/不指定: 同时采访两个平台，返回整合结果
            
        Returns:
            True 表示成功，False 表示失败
        """
        # 如果指定了平台，只采访该平台
        if platform in ("twitter", "reddit"):
            result = await self._interview_single_platform(agent_id, prompt, platform)
            
            if "error" in result:
                self.send_response(command_id, "failed", error=result["error"])
                print(f"  Interview失败: agent_id={agent_id}, platform={platform}, error={result['error']}")
                return False
            else:
                self.send_response(command_id, "completed", result=result)
                print(f"  Interview完成: agent_id={agent_id}, platform={platform}")
                return True
        
        # 未指定平台：同时采访两个平台
        if not self.twitter_env and not self.reddit_env:
            self.send_response(command_id, "failed", error="没有可用的模拟环境")
            return False
        
        results = {
            "agent_id": agent_id,
            "prompt": prompt,
            "platforms": {}
        }
        success_count = 0
        
        # 并行采访两个平台
        tasks = []
        platforms_to_interview = []
        
        if self.twitter_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "twitter"))
            platforms_to_interview.append("twitter")
        
        if self.reddit_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "reddit"))
            platforms_to_interview.append("reddit")
        
        # 并行执行
        platform_results = await asyncio.gather(*tasks)
        
        for platform_name, platform_result in zip(platforms_to_interview, platform_results):
            results["platforms"][platform_name] = platform_result
            if "error" not in platform_result:
                success_count += 1
        
        if success_count > 0:
            self.send_response(command_id, "completed", result=results)
            print(f"  Interview完成: agent_id={agent_id}, 成功平台数={success_count}/{len(platforms_to_interview)}")
            return True
        else:
            errors = [f"{p}: {r.get('error', '未知错误')}" for p, r in results["platforms"].items()]
            self.send_response(command_id, "failed", error="; ".join(errors))
            print(f"  Interview失败: agent_id={agent_id}, 所有平台都失败")
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict], platform: str = None) -> bool:
        """
        处理批量采访命令
        
        Args:
            command_id: 命令ID
            interviews: [{"agent_id": int, "prompt": str, "platform": str(optional)}, ...]
            platform: 默认平台（可被每个interview项覆盖）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None/不指定: 每个Agent同时采访两个平台
        """
        # 按平台分组
        twitter_interviews = []
        reddit_interviews = []
        both_platforms_interviews = []  # 需要同时采访两个平台的
        
        for interview in interviews:
            item_platform = interview.get("platform", platform)
            if item_platform == "twitter":
                twitter_interviews.append(interview)
            elif item_platform == "reddit":
                reddit_interviews.append(interview)
            else:
                # 未指定平台：两个平台都采访
                both_platforms_interviews.append(interview)
        
        # 把 both_platforms_interviews 拆分到两个平台
        if both_platforms_interviews:
            if self.twitter_env:
                twitter_interviews.extend(both_platforms_interviews)
            if self.reddit_env:
                reddit_interviews.extend(both_platforms_interviews)
        
        results = {}
        
        # 处理Twitter平台的采访
        if twitter_interviews and self.twitter_env:
            try:
                twitter_actions = {}
                for interview in twitter_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.twitter_agent_graph.get_agent(agent_id)
                        twitter_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  警告: 无法获取Twitter Agent {agent_id}: {e}")
                
                if twitter_actions:
                    await self.twitter_env.step(twitter_actions)
                    
                    for interview in twitter_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "twitter")
                        result["platform"] = "twitter"
                        results[f"twitter_{agent_id}"] = result
            except Exception as e:
                print(f"  Twitter批量Interview失败: {e}")
        
        # 处理Reddit平台的采访
        if reddit_interviews and self.reddit_env:
            try:
                reddit_actions = {}
                for interview in reddit_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.reddit_agent_graph.get_agent(agent_id)
                        reddit_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  警告: 无法获取Reddit Agent {agent_id}: {e}")
                
                if reddit_actions:
                    await self.reddit_env.step(reddit_actions)
                    
                    for interview in reddit_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "reddit")
                        result["platform"] = "reddit"
                        results[f"reddit_{agent_id}"] = result
            except Exception as e:
                print(f"  Reddit批量Interview失败: {e}")
        
        if results:
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  批量Interview完成: {len(results)} 个Agent")
            return True
        else:
            self.send_response(command_id, "failed", error="没有成功的采访")
            return False
    
    def _get_interview_result(self, agent_id: int, platform: str) -> Dict[str, Any]:
        """从数据库获取最新的Interview结果"""
        db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")
        
        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }
        
        if not os.path.exists(db_path):
            return result
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 查询最新的Interview记录
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))
            
            row = cursor.fetchone()
            if row:
                user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json
            
            conn.close()
            
        except Exception as e:
            print(f"  读取Interview结果失败: {e}")
        
        return result
    
    async def process_commands(self) -> bool:
        """
        处理所有待处理命令
        
        Returns:
            True 表示继续运行，False 表示应该退出
        """
        command = self.poll_command()
        if not command:
            return True
        
        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})
        
        print(f"\n收到IPC命令: {command_type}, id={command_id}")
        
        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", ""),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", []),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.CLOSE_ENV:
            print("收到关闭环境命令")
            self.send_response(command_id, "completed", result={"message": "环境即将关闭"})
            return False
        
        else:
            self.send_response(command_id, "failed", error=f"未知命令类型: {command_type}")
            return True


def load_config(config_path: str) -> Dict[str, Any]:
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# 需要过滤掉的非核心动作类型（这些动作对分析价值较低）
FILTERED_ACTIONS = {'refresh', 'sign_up'}

# 动作类型映射表（数据库中的名称 -> 标准名称）
ACTION_TYPE_MAP = {
    'create_post': 'CREATE_POST',
    'like_post': 'LIKE_POST',
    'dislike_post': 'DISLIKE_POST',
    'repost': 'REPOST',
    'quote_post': 'QUOTE_POST',
    'follow': 'FOLLOW',
    'mute': 'MUTE',
    'create_comment': 'CREATE_COMMENT',
    'like_comment': 'LIKE_COMMENT',
    'dislike_comment': 'DISLIKE_COMMENT',
    'search_posts': 'SEARCH_POSTS',
    'search_user': 'SEARCH_USER',
    'trend': 'TREND',
    'do_nothing': 'DO_NOTHING',
    'interview': 'INTERVIEW',
}


def get_agent_names_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """
    从 simulation_config 中获取 agent_id -> entity_name 的映射
    
    这样可以在 actions.jsonl 中显示真实的实体名称，而不是 "Agent_0" 这样的代号
    
    Args:
        config: simulation_config.json 的内容
        
    Returns:
        agent_id -> entity_name 的映射字典
    """
    agent_names = {}
    agent_configs = config.get("agent_configs", [])
    
    for agent_config in agent_configs:
        agent_id = agent_config.get("agent_id")
        entity_name = agent_config.get("entity_name", f"Agent_{agent_id}")
        if agent_id is not None:
            agent_names[agent_id] = entity_name
    
    return agent_names


def fetch_new_actions_from_db(
    db_path: str,
    last_rowid: int,
    agent_names: Dict[int, str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    从数据库中获取新的动作记录，并补充完整的上下文信息
    
    Args:
        db_path: 数据库文件路径
        last_rowid: 上次读取的最大 rowid 值（使用 rowid 而不是 created_at，因为不同平台的 created_at 格式不同）
        agent_names: agent_id -> agent_name 映射
        
    Returns:
        (actions_list, new_last_rowid)
        - actions_list: 动作列表，每个元素包含 agent_id, agent_name, action_type, action_args（含上下文信息）
        - new_last_rowid: 新的最大 rowid 值
    """
    actions = []
    new_last_rowid = last_rowid
    
    if not os.path.exists(db_path):
        return actions, new_last_rowid
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 使用 rowid 来追踪已处理的记录（rowid 是 SQLite 的内置自增字段）
        # 这样可以避免 created_at 格式差异问题（Twitter 用整数，Reddit 用日期时间字符串）
        cursor.execute("""
            SELECT rowid, user_id, action, info
            FROM trace
            WHERE rowid > ?
            ORDER BY rowid ASC
        """, (last_rowid,))
        
        for rowid, user_id, action, info_json in cursor.fetchall():
            # 更新最大 rowid
            new_last_rowid = rowid
            
            # 过滤非核心动作
            if action in FILTERED_ACTIONS:
                continue
            
            # 解析动作参数
            try:
                action_args = json.loads(info_json) if info_json else {}
            except json.JSONDecodeError:
                action_args = {}
            
            # 精简 action_args，只保留关键字段（保留完整内容，不截断）
            simplified_args = {}
            if 'content' in action_args:
                simplified_args['content'] = action_args['content']
            if 'post_id' in action_args:
                simplified_args['post_id'] = action_args['post_id']
            if 'comment_id' in action_args:
                simplified_args['comment_id'] = action_args['comment_id']
            if 'quoted_id' in action_args:
                simplified_args['quoted_id'] = action_args['quoted_id']
            if 'new_post_id' in action_args:
                simplified_args['new_post_id'] = action_args['new_post_id']
            if 'follow_id' in action_args:
                simplified_args['follow_id'] = action_args['follow_id']
            if 'query' in action_args:
                simplified_args['query'] = action_args['query']
            if 'like_id' in action_args:
                simplified_args['like_id'] = action_args['like_id']
            if 'dislike_id' in action_args:
                simplified_args['dislike_id'] = action_args['dislike_id']
            
            # 转换动作类型名称
            action_type = ACTION_TYPE_MAP.get(action, action.upper())
            
            # 补充上下文信息（帖子内容、用户名等）
            _enrich_action_context(cursor, action_type, simplified_args, agent_names)
            
            actions.append({
                'agent_id': user_id,
                'agent_name': agent_names.get(user_id, f'Agent_{user_id}'),
                'action_type': action_type,
                'action_args': simplified_args,
            })
        
        conn.close()
    except Exception as e:
        print(f"读取数据库动作失败: {e}")
    
    return actions, new_last_rowid


def _enrich_action_context(
    cursor,
    action_type: str,
    action_args: Dict[str, Any],
    agent_names: Dict[int, str]
) -> None:
    """
    为动作补充上下文信息（帖子内容、用户名等）
    
    Args:
        cursor: 数据库游标
        action_type: 动作类型
        action_args: 动作参数（会被修改）
        agent_names: agent_id -> agent_name 映射
    """
    try:
        # 点赞/踩帖子：补充帖子内容和作者
        if action_type in ('LIKE_POST', 'DISLIKE_POST'):
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
        
        # 转发帖子：补充原帖内容和作者
        elif action_type == 'REPOST':
            new_post_id = action_args.get('new_post_id')
            if new_post_id:
                # 转发帖子的 original_post_id 指向原帖
                cursor.execute("""
                    SELECT original_post_id FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    original_post_id = row[0]
                    original_info = _get_post_info(cursor, original_post_id, agent_names)
                    if original_info:
                        action_args['original_content'] = original_info.get('content', '')
                        action_args['original_author_name'] = original_info.get('author_name', '')
        
        # 引用帖子：补充原帖内容、作者和引用评论
        elif action_type == 'QUOTE_POST':
            quoted_id = action_args.get('quoted_id')
            new_post_id = action_args.get('new_post_id')
            
            if quoted_id:
                original_info = _get_post_info(cursor, quoted_id, agent_names)
                if original_info:
                    action_args['original_content'] = original_info.get('content', '')
                    action_args['original_author_name'] = original_info.get('author_name', '')
            
            # 获取引用帖子的评论内容（quote_content）
            if new_post_id:
                cursor.execute("""
                    SELECT quote_content FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    action_args['quote_content'] = row[0]
        
        # 关注用户：补充被关注用户的名称
        elif action_type == 'FOLLOW':
            follow_id = action_args.get('follow_id')
            if follow_id:
                # 从 follow 表获取 followee_id
                cursor.execute("""
                    SELECT followee_id FROM follow WHERE follow_id = ?
                """, (follow_id,))
                row = cursor.fetchone()
                if row:
                    followee_id = row[0]
                    target_name = _get_user_name(cursor, followee_id, agent_names)
                    if target_name:
                        action_args['target_user_name'] = target_name
        
        # 屏蔽用户：补充被屏蔽用户的名称
        elif action_type == 'MUTE':
            # 从 action_args 中获取 user_id 或 target_id
            target_id = action_args.get('user_id') or action_args.get('target_id')
            if target_id:
                target_name = _get_user_name(cursor, target_id, agent_names)
                if target_name:
                    action_args['target_user_name'] = target_name
        
        # 点赞/踩评论：补充评论内容和作者
        elif action_type in ('LIKE_COMMENT', 'DISLIKE_COMMENT'):
            comment_id = action_args.get('comment_id')
            if comment_id:
                comment_info = _get_comment_info(cursor, comment_id, agent_names)
                if comment_info:
                    action_args['comment_content'] = comment_info.get('content', '')
                    action_args['comment_author_name'] = comment_info.get('author_name', '')
        
        # 发表评论：补充所评论的帖子信息
        elif action_type == 'CREATE_COMMENT':
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
    
    except Exception as e:
        # 补充上下文失败不影响主流程
        print(f"补充动作上下文失败: {e}")


def _get_post_info(
    cursor,
    post_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    获取帖子信息
    
    Args:
        cursor: 数据库游标
        post_id: 帖子ID
        agent_names: agent_id -> agent_name 映射
        
    Returns:
        包含 content 和 author_name 的字典，或 None
    """
    try:
        cursor.execute("""
            SELECT p.content, p.user_id, u.agent_id
            FROM post p
            LEFT JOIN user u ON p.user_id = u.user_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # 优先使用 agent_names 中的名称
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # 从 user 表获取名称
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def _get_user_name(
    cursor,
    user_id: int,
    agent_names: Dict[int, str]
) -> Optional[str]:
    """
    获取用户名称
    
    Args:
        cursor: 数据库游标
        user_id: 用户ID
        agent_names: agent_id -> agent_name 映射
        
    Returns:
        用户名称，或 None
    """
    try:
        cursor.execute("""
            SELECT agent_id, name, user_name FROM user WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            agent_id = row[0]
            name = row[1]
            user_name = row[2]
            
            # 优先使用 agent_names 中的名称
            if agent_id is not None and agent_id in agent_names:
                return agent_names[agent_id]
            return name or user_name or ''
    except Exception:
        pass
    return None


def _get_comment_info(
    cursor,
    comment_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    获取评论信息
    
    Args:
        cursor: 数据库游标
        comment_id: 评论ID
        agent_names: agent_id -> agent_name 映射
        
    Returns:
        包含 content 和 author_name 的字典，或 None
    """
    try:
        cursor.execute("""
            SELECT c.content, c.user_id, u.agent_id
            FROM comment c
            LEFT JOIN user u ON c.user_id = u.user_id
            WHERE c.comment_id = ?
        """, (comment_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # 优先使用 agent_names 中的名称
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # 从 user 表获取名称
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def create_model(config: Dict[str, Any], use_boost: bool = False):
    """创建LLM模型。

    委托给 app.utils.oasis_llm.create_oasis_model，按 LLM_PROVIDER 选择后端：
    - claude-cli / codex-cli: CLI 桥接（默认）
    - openai: ModelFactory OpenAI 路径，并保留双 LLM（boost）加速配置
      （通用配置 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL_NAME，
       加速配置 LLM_BOOST_API_KEY/LLM_BOOST_BASE_URL/LLM_BOOST_MODEL_NAME）

    Args:
        config: 模拟配置字典
        use_boost: openai 提供方时是否使用加速 LLM 配置（如果可用）
    """
    return create_oasis_model(config=config, use_boost=use_boost)


def _weighted_sample_without_replacement(items: List, k: int) -> List:
    """按权重不放回采样 k 个（Efraimidis-Spirakis A-Res）。items: [(id, weight), ...]。"""
    if not items or k <= 0:
        return []
    if k >= len(items):
        return [i for i, _ in items]
    keyed = []
    for item_id, w in items:
        w = max(1e-6, float(w))
        keyed.append((_RNG.random() ** (1.0 / w), item_id))  # EXECPLAN2 I-7-2: 可复现采样
    keyed.sort(reverse=True)
    return [item_id for _, item_id in keyed[:k]]


def get_active_agents_for_round(
    env,
    config: Dict[str, Any],
    current_hour: int,
    round_num: int,
    last_active_ids: Optional[set] = None,
) -> List:
    """根据时间、影响力与上一轮活跃情况决定本轮激活哪些 Agent（T3.5）。

    - 激活概率 p = activity_level × (0.5 + 0.5 × 归一化影响力) × 时段乘子；
    - 上一轮活跃/被提及的 agent 获 ×1.5 近因加成（形成级联）；
    - 目标人数随 cast 规模放大（max(base_max, ceil(0.2N))，封顶 3×base_max），避免大规模 cast 被饿死；
    - 候选按影响力加权不放回采样。缺 influence_weight 时退化为 1.0（与旧行为接近）。
    """
    time_config = config.get("time_config", {})
    agent_configs = config.get("agent_configs", [])
    num_agents = len(agent_configs)
    last_active_ids = last_active_ids or set()

    base_max = time_config.get("agents_per_hour_max", 20)

    peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])

    if current_hour in peak_hours:
        multiplier = time_config.get("peak_activity_multiplier", 1.5)
    elif current_hour in off_peak_hours:
        multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
    else:
        multiplier = 1.0

    # 影响力归一化（用于激活概率与采样权重）
    influences = [float(c.get("influence_weight", 1.0) or 1.0) for c in agent_configs]
    max_infl = max(influences) if influences else 1.0
    if max_infl <= 0:
        max_infl = 1.0

    candidates = []  # (agent_id, influence_weight)
    for cfg in agent_configs:
        agent_id = cfg.get("agent_id", 0)
        active_hours = cfg.get("active_hours", list(range(8, 23)))
        if current_hour not in active_hours:
            continue
        activity_level = float(cfg.get("activity_level", 0.5) or 0.5)
        infl = float(cfg.get("influence_weight", 1.0) or 1.0)
        p = activity_level * (0.5 + 0.5 * (infl / max_infl)) * multiplier
        if agent_id in last_active_ids:
            p *= 1.5  # 近因加成：上一轮活跃/被提及 → 形成级联
        if _RNG.random() < min(1.0, p):  # EXECPLAN2 I-7-2: 可复现激活概率
            candidates.append((agent_id, infl))

    # 目标人数：随 cast 规模放大，封顶 3×base_max
    base_target = max(base_max, (num_agents + 4) // 5)  # ceil(0.2 * num_agents)
    target_count = max(1, int(min(base_max * 3, base_target) * multiplier))

    selected_ids = _weighted_sample_without_replacement(candidates, target_count)

    active_agents = []
    for agent_id in selected_ids:
        try:
            agent = env.agent_graph.get_agent(agent_id)
            active_agents.append((agent_id, agent))
        except Exception:
            pass

    return active_agents


# ============================================================================
# EXECPLAN2 I-2-1: per-agent affective-state dynamics (mood/energy/opinion/fatigue)
# threaded into each round's prompt. All gated behind Config.SIM_AGENT_DYNAMICS;
# when off these helpers are never called → static-persona behavior is byte-identical.
# The pure update math lives in app.services.agent_dynamics (offline-tested); here
# we (a) extract received-interaction signals from the round's actions, (b) inject
# the rendered state line into each active agent's system message before env.step.
# ============================================================================
def _build_dynamics_tracker(config, log_info):
    """Build an AgentDynamicsTracker if SIM_AGENT_DYNAMICS is on; else None (no-op)."""
    try:
        from app.config import Config
        if not getattr(Config, "SIM_AGENT_DYNAMICS", False):
            return None
        from app.services.agent_dynamics import AgentDynamicsTracker
        tracker = AgentDynamicsTracker.from_config(config.get("agent_configs", []), config_obj=Config)
        log_info("已启用逐智能体动态情感状态 (SIM_AGENT_DYNAMICS)")
        return tracker
    except Exception as e:  # never let an init bug break the run
        log_info(f"动态情感状态初始化失败，按静态人设运行: {e}")
        return None


def _inject_agent_dynamics(active_agents, tracker, log_info):
    """Append each active agent's current-state line as a fresh SYSTEM memory record
    for this round. astep() reads memory.get_context(), so the note becomes the
    most-recent system message before the action prompt (maximal salience) WITHOUT
    clearing the agent's accumulated conversation memory. Best-effort per agent.

    (EXECPLAN2 I-2-1. Memory-preserving via update_memory rather than the earlier
    init_messages() re-seed, which the adversarial review flagged as silently wiping
    cross-round agent memory once an agent developed non-baseline state.)"""
    if tracker is None:
        return
    try:
        from camel.messages import BaseMessage
        from camel.types import OpenAIBackendRole
    except Exception:
        return
    for aid, agent in active_agents:
        try:
            line = tracker.state_line(aid)
            if not line:
                continue
            note = BaseMessage.make_user_message(role_name="StateUpdater", content=line)
            agent.update_memory(note, OpenAIBackendRole.SYSTEM)
        except Exception as e:
            log_info(f"动态状态注入失败 (agent {aid}): {e}")


def _observe_agent_dynamics(tracker, actual_actions, name_to_id):
    """Feed this round's signals into the tracker (best-effort, never raises)."""
    if tracker is None:
        return
    try:
        from app.services.agent_dynamics import extract_round_signals
        received, activity = extract_round_signals(actual_actions, name_to_id)
        tracker.observe_round(received, activity)
    except Exception:
        pass


async def inject_initial_follows(env, event_config, log_info, agent_names=None, action_logger=None):
    """T3.3: 把 event_config.initial_follows 作为 round-0 关注边注入。

    对每条 [follower, followee]：
      1. ``agent_graph.add_edge`` 直接建图——立刻改变推荐器看到的社交结构；
      2. 下一个 ``FOLLOW`` ManualAction——写入 ``follow`` 表（platform.follow 自带去重）。
    两者互不冲突（add_edge 改内存图，follow 写 DB 表）。一个 agent 每个 step 只能一个动作，
    故按 follower 分组多趟 step 清空。空/缺失 → 直接返回 0（与旧行为一致）。
    """
    follows = event_config.get("initial_follows", []) or []
    if not follows:
        return 0

    from collections import defaultdict
    by_follower = defaultdict(list)
    for pair in follows:
        try:
            follower, followee = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if follower == followee:
            continue
        # 1. 直接建图（best-effort）
        try:
            env.agent_graph.add_edge(follower, followee)
        except Exception:
            pass
        by_follower[follower].append(followee)

    # 2. FOLLOW 动作多趟注入（每趟每个 follower 处理一个 followee）
    applied = 0
    max_passes = max((len(v) for v in by_follower.values()), default=0)
    for p in range(max_passes):
        actions = {}
        for follower, followees in by_follower.items():
            if p >= len(followees):
                continue
            try:
                agent = env.agent_graph.get_agent(follower)
                actions[agent] = ManualAction(
                    action_type=ActionType.FOLLOW,
                    action_args={"followee_id": followees[p]},
                )
            except Exception:
                continue
        if not actions:
            continue
        try:
            await env.step(actions)
            applied += len(actions)
        except Exception as e:
            log_info(f"初始关注注入第 {p + 1} 趟部分失败（继续）: {e}")
    log_info(f"已建立 {applied} 条初始关注边")
    return applied


async def fire_scheduled_events(env, event_config, loop_round, agent_names, action_logger, log_info):
    """T3.8: 在 loop_round（0 基）触发 scheduled_events 中 round==loop_round 的事件为 CREATE_POST。

    复用 initial_posts 的注入路径（matched poster → ManualAction(CREATE_POST)）。无匹配事件 → 0。
    返回成功触发的事件数。
    """
    events = event_config.get("scheduled_events", []) or []
    due = [e for e in events if int(e.get("round", -1)) == loop_round]
    if not due:
        return 0
    actions = {}
    fired = 0
    for ev in due:
        agent_id = ev.get("poster_agent_id")
        content = str(ev.get("content", "") or "")
        if agent_id is None or not content:
            continue
        try:
            agent = env.agent_graph.get_agent(agent_id)
            actions[agent] = ManualAction(
                action_type=ActionType.CREATE_POST,
                action_args={"content": content},
            )
            if action_logger:
                action_logger.log_action(
                    round_num=loop_round + 1,
                    agent_id=agent_id,
                    agent_name=(agent_names or {}).get(agent_id, f"Agent_{agent_id}"),
                    action_type="CREATE_POST",
                    action_args={"content": content, "is_scheduled_event": True},
                )
            fired += 1
        except Exception:
            continue
    if actions:
        try:
            await env.step(actions)
            log_info(f"第 {loop_round + 1} 轮触发 {fired} 个定时事件（时间线回放）")
        except Exception as e:
            log_info(f"定时事件触发失败，跳过（不中断模拟）: {e}")
            return 0
    return fired


def build_oasis_platform(kind: str, db_path: str, config: Dict[str, Any], log_info):
    """T3.12: 用 config 的 *_config recsys 旋钮构建自定义 oasis Platform（含 T3.9 时钟锚定）。

    OASIS 的 make() 只接受 agent_graph/platform/db/semaphore；recsys_type / refresh_rec_post_count /
    max_rec_post_len / following_post_count / start_time 等仅在 Platform 上。仅当 SIM_WIRE_RECSYS=true
    时启用；否则返回 None，调用方退回 DefaultPlatformType（与旧行为逐字节一致）。构建失败同样回退。

    映射：recsys_type / refresh_rec_post_count / max_rec_post_len 直传；echo_chamber_strength →
    following_post_count（越强→关注流曝光越多→同温层更强）。recency/popularity/relevance/
    viral_threshold 是展示性权重，OASIS 不消费，此处不再谎称生效。
    """
    if os.environ.get("SIM_WIRE_RECSYS", "false").strip().lower() != "true":
        return None
    try:
        from oasis.social_platform.platform import Platform
        from oasis.social_platform.channel import Channel
    except Exception as e:  # noqa: BLE001
        log_info(f"recsys 旋钮接入不可用，回退默认平台: {e}")
        return None

    pcfg = (config.get("twitter_config") if kind == "twitter" else config.get("reddit_config")) or {}
    echo = float(pcfg.get("echo_chamber_strength", 0.5) or 0.5)
    start_time = None
    as_of = config.get("as_of_date")
    if as_of:
        try:
            start_time = datetime.fromisoformat(str(as_of)[:10])
        except Exception:
            start_time = None
    try:
        channel = Channel()
        if kind == "twitter":
            platform = Platform(
                db_path=db_path,
                channel=channel,
                recsys_type=(pcfg.get("recsys_type") or "twhin-bert"),
                refresh_rec_post_count=int(pcfg.get("refresh_rec_post_count") or 2),
                max_rec_post_len=int(pcfg.get("max_rec_post_len") or 2),
                following_post_count=int(round(2 + echo * 4)),
                start_time=start_time,
            )
        else:
            platform = Platform(
                db_path=db_path,
                channel=channel,
                recsys_type=(pcfg.get("recsys_type") or "reddit"),
                allow_self_rating=True,
                show_score=True,
                refresh_rec_post_count=int(pcfg.get("refresh_rec_post_count") or 5),
                max_rec_post_len=int(pcfg.get("max_rec_post_len") or 100),
                start_time=start_time,
            )
        log_info(
            f"已接入 recsys 旋钮[{kind}]: type={getattr(platform, 'recsys_type', '?')}, "
            f"echo={echo}, start_time={start_time.date() if start_time else '默认'}"
        )
        return platform
    except Exception as e:  # noqa: BLE001
        log_info(f"自定义平台构建失败，回退默认平台: {e}")
        return None


# ============================================================
# EXECPLAN2 I-2-0: 涌现结构 / 观点动力学度量层
# ------------------------------------------------------------
# 模拟结束后（只读地）在 {platform}_simulation.db + actions.jsonl 上计算：
#   1) 观点/立场轨迹 —— 每轮按 stance 桶（supportive/opposing/neutral/observer）
#      统计发声量（CREATE_POST/CREATE_COMMENT/QUOTE_POST），并对内容做轻量情感打分；
#   2) 极化指数 —— 每 agent 净情感分布的方差/双峰性 + 跨立场 vs 同立场互动比；
#   3) 关注图社区检测 —— 在真实 follow 表上跑 networkx（贪婪模块度/标签传播），
#      给出每个社区的主导立场与桥接 agent；
#   4) 级联/传播 —— top 帖的回复+转发+引用深度与广度。
# 全部行为默认关闭（SIM_EMERGENT_METRICS!=true 时不计算），networkx 缺失时社区检测
# 自动跳过，情感使用离线 CN/EN 极性词典（无 LLM 成本）。任何异常都不影响已完成的模拟，
# 仅在调用方以 try/except 包裹，结果写入 {platform}_emergent_metrics.json 与 emergent_metrics.json。
# ============================================================

# 影响立场表达的发声动作（用于立场轨迹加权与跨立场互动判定）
_SPEECH_ACTIONS = {"CREATE_POST", "CREATE_COMMENT", "QUOTE_POST", "REPOST"}
# 直接产生“agent→agent”互动的动作（用于跨立场 vs 同立场互动比）
_INTERACTION_ACTIONS = {
    "REPOST", "QUOTE_POST", "CREATE_COMMENT", "LIKE_POST", "DISLIKE_POST",
    "LIKE_COMMENT", "DISLIKE_COMMENT", "FOLLOW", "MUTE",
}
_STANCE_BUCKETS = ("supportive", "opposing", "neutral", "observer")

# 离线情感词典（CN + EN 极性词），用于 LLM 成本过高时的回退打分。
# 仅作粗粒度净情感方向估计；命中即 ±1，无命中记 0（中性）。
_POS_LEXICON = {
    # EN
    "good", "great", "excellent", "positive", "support", "growth", "win", "gain",
    "strong", "bullish", "optimistic", "agree", "love", "best", "advantage",
    "lead", "leading", "dominate", "success", "breakthrough", "soar", "surge",
    # CN
    "好", "强", "优势", "增长", "领先", "成功", "突破", "看好", "利好", "支持",
    "同意", "赞", "上涨", "飙升", "主导", "碾压", "双赢", "乐观",
}
_NEG_LEXICON = {
    # EN
    "bad", "poor", "negative", "oppose", "decline", "lose", "loss", "weak",
    "bearish", "pessimistic", "disagree", "hate", "worst", "risk", "fail",
    "failure", "crash", "plunge", "drop", "threat", "concern", "doubt",
    # CN
    "差", "弱", "下跌", "崩", "失败", "风险", "反对", "不同意", "看空", "利空",
    "暴跌", "担忧", "质疑", "威胁", "落后", "亏损", "悲观", "泡沫",
}


def _load_stance_by_agent(config: Dict[str, Any]) -> Dict[int, str]:
    """agent_id -> 归一化 stance 桶映射（缺省 neutral）。"""
    stance_map: Dict[int, str] = {}
    for cfg in config.get("agent_configs", []) or []:
        aid = cfg.get("agent_id")
        if aid is None:
            continue
        raw = str(cfg.get("stance", "neutral") or "neutral").strip().lower()
        stance_map[int(aid)] = raw if raw in _STANCE_BUCKETS else "neutral"
    return stance_map


def _lexicon_sentiment(text: str) -> float:
    """离线极性打分：返回 [-1, 1] 的净情感方向（无命中 → 0）。"""
    if not text:
        return 0.0
    low = text.lower()
    pos = sum(1 for w in _POS_LEXICON if w in low)
    neg = sum(1 for w in _NEG_LEXICON if w in low)
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / float(pos + neg)


def _iter_action_lines(actions_jsonl_path: str):
    """逐行产出 actions.jsonl 中的“真实动作”记录（跳过 event_type 控制行）。"""
    if not os.path.exists(actions_jsonl_path):
        return
    try:
        with open(actions_jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 控制行（simulation_start/round_start/round_end/simulation_end）无 action_type
                if rec.get("event_type") or not rec.get("action_type"):
                    continue
                yield rec
    except OSError:
        return


def _score_stance_trajectory(
    actions_jsonl_path: str,
    stance_by_agent: Dict[int, str],
) -> Tuple[List[Dict[str, Any]], float, Dict[int, float]]:
    """计算每轮立场轨迹 + 全局极化指数（按 agent 净情感分布方差）。

    Returns:
        (trajectory, polarization_index, net_sentiment_by_agent)
        - trajectory: [{round, by_stance:{...发声量...}, net_sentiment}]
        - polarization_index: agent 级净情感分布的方差（[0,1] 量级，越大越极化）
        - net_sentiment_by_agent: agent_id -> 平均净情感（供互动比/社区主导立场使用）
    """
    from collections import defaultdict

    per_round: Dict[int, Dict[str, Any]] = {}
    sent_sum_by_agent: Dict[int, float] = defaultdict(float)
    sent_cnt_by_agent: Dict[int, int] = defaultdict(int)

    for rec in _iter_action_lines(actions_jsonl_path):
        action_type = str(rec.get("action_type", ""))
        if action_type not in _SPEECH_ACTIONS:
            continue
        agent_id = rec.get("agent_id")
        round_num = rec.get("round")
        if agent_id is None or round_num is None:
            continue
        agent_id = int(agent_id)
        round_num = int(round_num)
        stance = stance_by_agent.get(agent_id, "neutral")

        bucket = per_round.setdefault(
            round_num,
            {"round": round_num, "by_stance": {s: 0 for s in _STANCE_BUCKETS}, "_sent": 0.0, "_n": 0},
        )
        bucket["by_stance"][stance] = bucket["by_stance"].get(stance, 0) + 1

        # 情感仅对带内容的动作打分
        content = ""
        args = rec.get("action_args") or {}
        if isinstance(args, dict):
            content = str(args.get("content") or args.get("quote_content") or "")
        if content:
            s = _lexicon_sentiment(content)
            bucket["_sent"] += s
            bucket["_n"] += 1
            sent_sum_by_agent[agent_id] += s
            sent_cnt_by_agent[agent_id] += 1

    trajectory: List[Dict[str, Any]] = []
    for round_num in sorted(per_round.keys()):
        b = per_round[round_num]
        net = (b["_sent"] / b["_n"]) if b["_n"] else 0.0
        trajectory.append({
            "round": round_num,
            "by_stance": b["by_stance"],
            "net_sentiment": round(net, 4),
        })

    net_by_agent: Dict[int, float] = {}
    for aid, total in sent_sum_by_agent.items():
        cnt = sent_cnt_by_agent.get(aid, 0)
        if cnt:
            net_by_agent[aid] = total / cnt

    # 极化指数：发声 agent 净情感分布的方差（0=完全一致，越大越极化）
    polarization = 0.0
    vals = list(net_by_agent.values())
    if len(vals) >= 2:
        mean = sum(vals) / len(vals)
        polarization = sum((v - mean) ** 2 for v in vals) / len(vals)

    return trajectory, round(polarization, 4), net_by_agent


def _compute_interaction_ratio(
    conn: "sqlite3.Connection",
    stance_by_agent: Dict[int, str],
) -> Dict[str, Any]:
    """跨立场 vs 同立场互动比。

    通过 follow（关注边）、post.original_post_id（转发/引用）、comment（回复）三类
    “agent→agent”边，按双方 stance 是否相同计数。比值 = 跨立场 / (同立场 + 跨立场)。
    """
    cursor = conn.cursor()

    # user_id -> agent_id -> stance
    uid_to_stance: Dict[int, str] = {}
    try:
        cursor.execute("SELECT user_id, agent_id FROM user")
        for user_id, agent_id in cursor.fetchall():
            if agent_id is None:
                continue
            uid_to_stance[user_id] = stance_by_agent.get(int(agent_id), "neutral")
    except sqlite3.Error:
        return {"cross_stance_interaction_ratio": 0.0, "cross_stance": 0, "within_stance": 0}

    cross = 0
    within = 0

    def _tally(src_uid, dst_uid):
        nonlocal cross, within
        if src_uid is None or dst_uid is None or src_uid == dst_uid:
            return
        s_src = uid_to_stance.get(src_uid)
        s_dst = uid_to_stance.get(dst_uid)
        if s_src is None or s_dst is None:
            return
        if s_src == s_dst:
            within += 1
        else:
            cross += 1

    # 关注边
    try:
        cursor.execute("SELECT follower_id, followee_id FROM follow")
        for follower, followee in cursor.fetchall():
            _tally(follower, followee)
    except sqlite3.Error:
        pass

    # 转发/引用边：reposter -> 原帖作者
    try:
        cursor.execute(
            "SELECT p.user_id, orig.user_id "
            "FROM post p JOIN post orig ON p.original_post_id = orig.post_id "
            "WHERE p.original_post_id IS NOT NULL"
        )
        for reposter, author in cursor.fetchall():
            _tally(reposter, author)
    except sqlite3.Error:
        pass

    # 评论边：评论者 -> 被评论帖作者
    try:
        cursor.execute(
            "SELECT c.user_id, p.user_id "
            "FROM comment c JOIN post p ON c.post_id = p.post_id"
        )
        for commenter, author in cursor.fetchall():
            _tally(commenter, author)
    except sqlite3.Error:
        pass

    total = cross + within
    ratio = (cross / total) if total else 0.0
    return {
        "cross_stance_interaction_ratio": round(ratio, 4),
        "cross_stance": cross,
        "within_stance": within,
    }


def _detect_follow_communities(
    conn: "sqlite3.Connection",
    stance_by_agent: Dict[int, str],
    log_info,
) -> List[Dict[str, Any]]:
    """在真实 follow 表上做社区检测（networkx 缺失 → 返回 [] 并跳过）。"""
    try:
        import networkx as nx  # 可选依赖，缺失即优雅跳过
        from networkx.algorithms import community as nx_community
    except Exception as e:  # noqa: BLE001
        log_info(f"涌现度量：networkx 不可用，跳过社区检测: {e}")
        return []

    cursor = conn.cursor()
    # user_id -> agent_id
    uid_to_agent: Dict[int, Optional[int]] = {}
    try:
        cursor.execute("SELECT user_id, agent_id FROM user")
        for user_id, agent_id in cursor.fetchall():
            uid_to_agent[user_id] = int(agent_id) if agent_id is not None else None
    except sqlite3.Error:
        return []

    g = nx.DiGraph()
    try:
        cursor.execute("SELECT follower_id, followee_id FROM follow")
        for follower, followee in cursor.fetchall():
            if follower is None or followee is None or follower == followee:
                continue
            g.add_edge(follower, followee)
    except sqlite3.Error:
        return []

    if g.number_of_nodes() == 0:
        return []

    # 贪婪模块度在无向图上运行；用无向投影
    ug = g.to_undirected()
    try:
        comms = list(nx_community.greedy_modularity_communities(ug))
    except Exception:
        try:
            comms = list(nx_community.label_propagation_communities(ug))
        except Exception as e:  # noqa: BLE001
            log_info(f"涌现度量：社区检测失败，跳过: {e}")
            return []

    # 桥接节点：跨社区出/入度高者（按 betweenness 近似——用跨社区边计数）
    node_to_comm: Dict[int, int] = {}
    for idx, members in enumerate(comms):
        for node in members:
            node_to_comm[node] = idx

    cross_links: Dict[int, int] = {}
    for u, v in ug.edges():
        if node_to_comm.get(u) != node_to_comm.get(v):
            cross_links[u] = cross_links.get(u, 0) + 1
            cross_links[v] = cross_links.get(v, 0) + 1

    result: List[Dict[str, Any]] = []
    for idx, members in enumerate(comms):
        member_agents = [uid_to_agent.get(uid) for uid in members]
        member_agents = [a for a in member_agents if a is not None]
        # 主导立场
        from collections import Counter
        stance_counts = Counter(stance_by_agent.get(a, "neutral") for a in member_agents)
        dominant = stance_counts.most_common(1)[0][0] if stance_counts else "neutral"
        # 桥接 agent（跨社区连接数最高的前 3 个）
        bridges = sorted(
            (uid for uid in members if cross_links.get(uid, 0) > 0),
            key=lambda uid: cross_links.get(uid, 0),
            reverse=True,
        )[:3]
        bridge_agents = [uid_to_agent.get(uid) for uid in bridges]
        bridge_agents = [a for a in bridge_agents if a is not None]
        result.append({
            "size": len(members),
            "members": sorted(member_agents),
            "dominant_stance": dominant,
            "stance_breakdown": dict(stance_counts),
            "bridge_agents": bridge_agents,
        })
    # 大社区在前
    result.sort(key=lambda c: c["size"], reverse=True)
    return result


def _compute_cascades(
    conn: "sqlite3.Connection",
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """每个原帖的级联/传播统计：回复数(breadth) + 转发/引用数 + 综合互动深度。"""
    cursor = conn.cursor()

    # 转发/引用：original_post_id -> 计数
    repost_counts: Dict[int, int] = {}
    try:
        cursor.execute(
            "SELECT original_post_id, COUNT(*) FROM post "
            "WHERE original_post_id IS NOT NULL GROUP BY original_post_id"
        )
        for orig_id, cnt in cursor.fetchall():
            if orig_id is not None:
                repost_counts[orig_id] = cnt
    except sqlite3.Error:
        repost_counts = {}

    # 评论数
    comment_counts: Dict[int, int] = {}
    try:
        cursor.execute("SELECT post_id, COUNT(*) FROM comment GROUP BY post_id")
        for post_id, cnt in cursor.fetchall():
            if post_id is not None:
                comment_counts[post_id] = cnt
    except sqlite3.Error:
        comment_counts = {}

    # 原帖（original_post_id 为空）的基础信息
    cascades: List[Dict[str, Any]] = []
    try:
        cursor.execute(
            "SELECT post_id, num_likes, num_shares FROM post "
            "WHERE original_post_id IS NULL"
        )
        rows = cursor.fetchall()
    except sqlite3.Error:
        return []

    for post_id, num_likes, num_shares in rows:
        reposts = repost_counts.get(post_id, 0)
        replies = comment_counts.get(post_id, 0)
        likes = num_likes or 0
        breadth = reposts + replies
        # 综合级联“深度”：直接互动总量（回复 + 转发 + 点赞）
        depth = breadth + likes
        if breadth == 0 and likes == 0:
            continue
        cascades.append({
            "post_id": post_id,
            "replies": replies,
            "reposts": reposts,
            "likes": likes,
            "breadth": breadth,
            "depth": depth,
        })

    cascades.sort(key=lambda c: c["depth"], reverse=True)
    return cascades[:top_n]


def compute_emergent_metrics(
    simulation_dir: str,
    config: Dict[str, Any],
    platform: str,
    log_info,
) -> Optional[Dict[str, Any]]:
    """EXECPLAN2 I-2-0: 为单个平台计算涌现度量并返回字典（失败 → None）。

    只读 {platform}_simulation.db + {platform}/actions.jsonl，绝不修改模拟产物。
    """
    db_path = os.path.join(simulation_dir, f"{platform}_simulation.db")
    actions_path = os.path.join(simulation_dir, platform, "actions.jsonl")
    if not os.path.exists(db_path):
        log_info(f"涌现度量[{platform}]：数据库不存在，跳过")
        return None

    stance_by_agent = _load_stance_by_agent(config)

    trajectory, polarization, net_by_agent = _score_stance_trajectory(actions_path, stance_by_agent)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        interaction = _compute_interaction_ratio(conn, stance_by_agent)
        communities = _detect_follow_communities(conn, stance_by_agent, log_info)
        cascades = _compute_cascades(conn)
    except Exception as e:  # noqa: BLE001
        log_info(f"涌现度量[{platform}]：数据库读取失败，部分跳过: {e}")
        interaction = {"cross_stance_interaction_ratio": 0.0, "cross_stance": 0, "within_stance": 0}
        communities = []
        cascades = []
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # 末轮立场占比（供报告 agent 直接引用“支持率从 X% 跌至 Y%”）
    final_stance_share: Dict[str, float] = {}
    if trajectory:
        last = trajectory[-1]["by_stance"]
        total = sum(last.values()) or 1
        final_stance_share = {s: round(v / total, 4) for s, v in last.items()}

    metrics = {
        "platform": platform,
        "polarization_index": polarization,
        "cross_stance_interaction_ratio": interaction.get("cross_stance_interaction_ratio", 0.0),
        "interaction_counts": {
            "cross_stance": interaction.get("cross_stance", 0),
            "within_stance": interaction.get("within_stance", 0),
        },
        "final_stance_share": final_stance_share,
        "stance_trajectory": trajectory,
        "follow_communities": communities,
        "cascades": cascades,
        "num_speaking_agents": len(net_by_agent),
        "sentiment_method": "lexicon",
    }
    return metrics


def write_emergent_metrics(
    simulation_dir: str,
    config: Dict[str, Any],
    platforms: List[str],
    log_info,
) -> None:
    """EXECPLAN2 I-2-0: 计算并原子写出涌现度量产物（仅 SIM_EMERGENT_METRICS=true 时调用）。

    产出：
      - {platform}_emergent_metrics.json（每平台）
      - emergent_metrics.json（聚合，供报告 agent 读取）
    全程 try/except 隔离，任何失败都不影响已完成的模拟。
    """
    from app.utils.atomic import write_json_atomic  # 复用原子写助手

    aggregate: Dict[str, Any] = {
        "simulation_id": config.get("simulation_id"),
        "generated_at": datetime.now().isoformat(),
        "platforms": {},
    }
    for platform in platforms:
        try:
            metrics = compute_emergent_metrics(simulation_dir, config, platform, log_info)
        except Exception as e:  # noqa: BLE001
            log_info(f"涌现度量[{platform}]：计算异常，跳过该平台: {e}")
            metrics = None
        if metrics is None:
            continue
        aggregate["platforms"][platform] = metrics
        try:
            write_json_atomic(
                os.path.join(simulation_dir, f"{platform}_emergent_metrics.json"),
                metrics,
            )
            log_info(
                f"涌现度量[{platform}]：极化={metrics['polarization_index']}, "
                f"跨立场互动比={metrics['cross_stance_interaction_ratio']}, "
                f"社区数={len(metrics['follow_communities'])}"
            )
        except Exception as e:  # noqa: BLE001
            log_info(f"涌现度量[{platform}]：写出失败: {e}")

    if aggregate["platforms"]:
        try:
            write_json_atomic(
                os.path.join(simulation_dir, "emergent_metrics.json"),
                aggregate,
            )
        except Exception as e:  # noqa: BLE001
            log_info(f"涌现度量：聚合产物写出失败: {e}")


class PlatformSimulation:
    """平台模拟结果容器"""
    def __init__(self):
        self.env = None
        self.agent_graph = None
        self.total_actions = 0


# ============== NEXTSTEPS P1-1/P1-2/P1-4: post-sim decision channel helpers ==============
def _read_actions_for_decision_channel(simulation_dir: str) -> List[Dict[str, Any]]:
    """读取两平台 actions.jsonl 的动作记录（跳过 round_start/end/sim_end 事件），
    汇成 [{round, agent_id, agent_name}] 供决策通道按轮回放。失败/缺失 → []。"""
    out: List[Dict[str, Any]] = []
    for plat in ("twitter", "reddit"):
        path = os.path.join(simulation_dir, plat, "actions.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if rec.get("event_type") or rec.get("agent_id") is None:
                        continue
                    out.append({"round": rec.get("round", 0),
                                "agent_id": rec.get("agent_id"),
                                "agent_name": rec.get("agent_name", "")})
        except OSError:
            continue
    return out


def _build_round_to_date(seed: Dict[str, Any], config: Dict[str, Any]):
    """NEXTSTEPS P1-2: round(1-based) → ISO 日期，as_of_date→horizon_date 线性映射。
    无法确定（缺日期/非法/horizon≤as_of）→ None（不做日期映射）。"""
    from datetime import datetime as _dt, timedelta as _td
    as_of, horizon = seed.get("as_of_date"), seed.get("horizon_date")
    if not as_of or not horizon:
        return None
    try:
        d0 = _dt.fromisoformat(str(as_of)[:10])
        d1 = _dt.fromisoformat(str(horizon)[:10])
    except (ValueError, TypeError):
        return None
    if d1 <= d0:
        return None
    tc = config.get("time_config", {}) if isinstance(config, dict) else {}
    try:
        total_hours = float(tc.get("total_simulation_hours", 72) or 72)
        mpr = float(tc.get("minutes_per_round", 60) or 60)
    except (TypeError, ValueError):
        total_hours, mpr = 72.0, 60.0
    total_rounds = max(1, int(total_hours * 60 / mpr))
    span_days = (d1 - d0).days

    def _r2d(rnd: int) -> str:
        frac = min(1.0, max(0.0, float(rnd) / total_rounds))
        return (d0 + _td(days=int(span_days * frac))).date().isoformat()

    return _r2d


async def run_twitter_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None,
    semaphore_platforms: int = 1
) -> PlatformSimulation:
    """运行Twitter模拟
    
    Args:
        config: 模拟配置
        simulation_dir: 模拟目录
        action_logger: 动作日志记录器
        main_logger: 主日志管理器
        max_rounds: 最大模拟轮数（可选，用于截断过长的模拟）
        
    Returns:
        PlatformSimulation: 包含env和agent_graph的结果对象
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Twitter] {msg}")
        print(f"[Twitter] {msg}")
    
    log_info("初始化...")
    
    # Twitter 使用通用 LLM 配置
    model = create_model(config, use_boost=False)
    
    # OASIS Twitter使用CSV格式
    profile_path = os.path.join(simulation_dir, "twitter_profiles.csv")
    if not os.path.exists(profile_path):
        log_info(f"错误: Profile文件不存在: {profile_path}")
        return result
    
    result.agent_graph = await generate_twitter_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=TWITTER_ACTIONS,
    )

    # EXECPLAN2 I-2-3: 按角色裁剪每个 Agent 的动作可供性（默认关闭；开关 SIM_ROLE_ACTION_PROFILES）。
    # 必须在 oasis.make()/env.reset() 之前，对 generate_*_agent_graph 产出的同一批 Agent 直接生效。
    if _role_action_profiles_enabled():
        try:
            _apply_role_action_profiles(result.agent_graph, config, TWITTER_ACTIONS, log_info)
        except Exception as _rap_err:  # noqa: BLE001
            log_info(f"角色动作可供性应用失败（已隔离，回退全局动作清单）: {_rap_err}")

    # 从配置文件获取 Agent 真实名称映射（使用 entity_name 而非默认的 Agent_X）
    agent_names = get_agent_names_from_config(config)
    # 如果配置中没有某个 agent，则使用 OASIS 的默认名称
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "twitter_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # T3.12: 优先用 config 的 recsys 旋钮构建自定义平台；未启用/失败则回退默认平台类型
    _custom_platform = build_oasis_platform("twitter", db_path, config, log_info)
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=_custom_platform if _custom_platform is not None else oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        semaphore=get_oasis_semaphore(config, use_boost=False, platforms=semaphore_platforms),  # 按提供方限制并发 LLM 请求数（双平台并行时各拿一半）
    )
    
    await result.env.reset()
    log_info("环境已启动")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # 跟踪数据库中最后处理的行号（使用 rowid 避免 created_at 格式差异）
    
    # 执行初始事件
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # 记录 round 0 开始（初始事件阶段）
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                # 修复：同一 agent 的多条初始帖此前会相互覆盖（dict 单键单值），第一条被静默丢弃。
                # 与 Reddit 路径一致地把同 agent 的多条合并成 list（env.step 接受 list 值）。
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )

                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass

        if initial_actions:
            try:
                await result.env.step(initial_actions)
                log_info(f"已发布 {len(initial_actions)} 条初始帖子")
            except Exception as init_err:
                log_info(f"初始帖子发布失败，跳过（继续进入主循环）: {init_err}")
    
    # T3.3: 注入研究/图谱驱动的初始关注边（建图 add_edge + FOLLOW 动作），让社交图不再从空起步
    try:
        await inject_initial_follows(result.env, event_config, log_info, agent_names, action_logger)
    except Exception as follow_err:
        log_info(f"初始关注注入失败，跳过（继续进入主循环）: {follow_err}")

    # 记录 round 0 结束
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)
    
    # 主模拟循环
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 60)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # 如果指定了最大轮数，则截断
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"轮数已截断: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
    
    start_time = datetime.now()
    
    last_active_ids: set = set()  # T3.5: 近因加成——上一轮活跃的 agent 下一轮更易被激活
    # I-2-1: 逐智能体动态情感状态（默认关；SIM_AGENT_DYNAMICS=true 时生效）
    dynamics_tracker = _build_dynamics_tracker(config, log_info)
    dyn_name_to_id = {name: aid for aid, name in agent_names.items()}
    for round_num in range(total_rounds):
        # 检查是否收到退出信号
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"收到退出信号，在第 {round_num + 1} 轮停止模拟")
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        # T3.8: 在活跃 agent 选择前回放本轮到期的研究时间线事件（CREATE_POST），使其对本轮可见
        try:
            fired = await fire_scheduled_events(
                result.env, event_config, round_num, agent_names, action_logger, log_info
            )
            total_actions += fired
        except Exception as _ev_err:
            log_info(f"定时事件触发异常，跳过（不中断模拟）: {_ev_err}")

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num, last_active_ids
        )
        last_active_ids = {aid for aid, _ in active_agents}  # T3.5: 供下一轮近因加成
        
        # 无论是否有活跃agent，都记录round开始
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # 没有活跃agent时也记录round结束（actions_count=0）
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue
        
        # I-2-1: 注入本轮动态情感状态到各活跃 agent 的系统提示（默认关 → no-op）
        _inject_agent_dynamics(active_agents, dynamics_tracker, log_info)
        actions = {agent: LLMAction() for _, agent in active_agents}
        # 健壮性：单次 env.step 内的某个 agent LLM 调用失败（超时/降级/异常）不应中断整场模拟。
        # 记录并跳过本轮，让模拟继续，保住此前所有轮次的进度。
        try:
            await result.env.step(actions)
        except Exception as step_err:
            log_info(f"第 {round_num + 1} 轮 env.step 失败，跳过本轮（不中断整场模拟）: {step_err}")
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        # 从数据库获取实际执行的动作并记录
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        # I-2-1: 用本轮实际动作更新动态情感状态（默认关 → no-op）
        _observe_agent_dynamics(dynamics_tracker, actual_actions, dyn_name_to_id)

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # 注意：不关闭环境，保留给Interview使用
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"模拟循环完成! 耗时: {elapsed:.1f}秒, 总动作: {total_actions}")
    
    return result


async def run_reddit_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None,
    semaphore_platforms: int = 1
) -> PlatformSimulation:
    """运行Reddit模拟
    
    Args:
        config: 模拟配置
        simulation_dir: 模拟目录
        action_logger: 动作日志记录器
        main_logger: 主日志管理器
        max_rounds: 最大模拟轮数（可选，用于截断过长的模拟）
        
    Returns:
        PlatformSimulation: 包含env和agent_graph的结果对象
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Reddit] {msg}")
        print(f"[Reddit] {msg}")
    
    log_info("初始化...")
    
    # Reddit 使用加速 LLM 配置（如果有的话，否则回退到通用配置）
    model = create_model(config, use_boost=True)
    
    profile_path = os.path.join(simulation_dir, "reddit_profiles.json")
    if not os.path.exists(profile_path):
        log_info(f"错误: Profile文件不存在: {profile_path}")
        return result
    
    result.agent_graph = await generate_reddit_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=REDDIT_ACTIONS,
    )

    # EXECPLAN2 I-2-3: 按角色裁剪每个 Agent 的动作可供性（默认关闭；开关 SIM_ROLE_ACTION_PROFILES）。
    # Reddit 平台以 REDDIT_ACTIONS 为 union，白名单与之求交，绝不授予平台不存在的动作。
    if _role_action_profiles_enabled():
        try:
            _apply_role_action_profiles(result.agent_graph, config, REDDIT_ACTIONS, log_info)
        except Exception as _rap_err:  # noqa: BLE001
            log_info(f"角色动作可供性应用失败（已隔离，回退全局动作清单）: {_rap_err}")

    # 从配置文件获取 Agent 真实名称映射（使用 entity_name 而非默认的 Agent_X）
    agent_names = get_agent_names_from_config(config)
    # 如果配置中没有某个 agent，则使用 OASIS 的默认名称
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "reddit_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # T3.12: 优先用 config 的 recsys 旋钮构建自定义平台；未启用/失败则回退默认平台类型
    _custom_platform = build_oasis_platform("reddit", db_path, config, log_info)
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=_custom_platform if _custom_platform is not None else oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=get_oasis_semaphore(config, use_boost=True, platforms=semaphore_platforms),  # 按提供方限制并发 LLM 请求数（双平台并行时各拿一半）
    )
    
    await result.env.reset()
    log_info("环境已启动")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # 跟踪数据库中最后处理的行号（使用 rowid 避免 created_at 格式差异）
    
    # 执行初始事件
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # 记录 round 0 开始（初始事件阶段）
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            try:
                await result.env.step(initial_actions)
                log_info(f"已发布 {len(initial_actions)} 条初始帖子")
            except Exception as init_err:
                log_info(f"初始帖子发布失败，跳过（继续进入主循环）: {init_err}")
    
    # T3.3: 注入研究/图谱驱动的初始关注边（建图 add_edge + FOLLOW 动作），让社交图不再从空起步
    try:
        await inject_initial_follows(result.env, event_config, log_info, agent_names, action_logger)
    except Exception as follow_err:
        log_info(f"初始关注注入失败，跳过（继续进入主循环）: {follow_err}")

    # 记录 round 0 结束
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)
    
    # 主模拟循环
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 60)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # 如果指定了最大轮数，则截断
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"轮数已截断: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
    
    start_time = datetime.now()
    
    last_active_ids: set = set()  # T3.5: 近因加成——上一轮活跃的 agent 下一轮更易被激活
    # I-2-1: 逐智能体动态情感状态（默认关；SIM_AGENT_DYNAMICS=true 时生效）
    dynamics_tracker = _build_dynamics_tracker(config, log_info)
    dyn_name_to_id = {name: aid for aid, name in agent_names.items()}
    for round_num in range(total_rounds):
        # 检查是否收到退出信号
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"收到退出信号，在第 {round_num + 1} 轮停止模拟")
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        # T3.8: 在活跃 agent 选择前回放本轮到期的研究时间线事件（CREATE_POST），使其对本轮可见
        try:
            fired = await fire_scheduled_events(
                result.env, event_config, round_num, agent_names, action_logger, log_info
            )
            total_actions += fired
        except Exception as _ev_err:
            log_info(f"定时事件触发异常，跳过（不中断模拟）: {_ev_err}")

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num, last_active_ids
        )
        last_active_ids = {aid for aid, _ in active_agents}  # T3.5: 供下一轮近因加成
        
        # 无论是否有活跃agent，都记录round开始
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # 没有活跃agent时也记录round结束（actions_count=0）
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue
        
        # I-2-1: 注入本轮动态情感状态到各活跃 agent 的系统提示（默认关 → no-op）
        _inject_agent_dynamics(active_agents, dynamics_tracker, log_info)
        actions = {agent: LLMAction() for _, agent in active_agents}
        # 健壮性：单次 env.step 内的某个 agent LLM 调用失败（超时/降级/异常）不应中断整场模拟。
        # 记录并跳过本轮，让模拟继续，保住此前所有轮次的进度。
        try:
            await result.env.step(actions)
        except Exception as step_err:
            log_info(f"第 {round_num + 1} 轮 env.step 失败，跳过本轮（不中断整场模拟）: {step_err}")
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        # 从数据库获取实际执行的动作并记录
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        # I-2-1: 用本轮实际动作更新动态情感状态（默认关 → no-op）
        _observe_agent_dynamics(dynamics_tracker, actual_actions, dyn_name_to_id)

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # 注意：不关闭环境，保留给Interview使用
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"模拟循环完成! 耗时: {elapsed:.1f}秒, 总动作: {total_actions}")
    
    return result


async def main():
    parser = argparse.ArgumentParser(description='OASIS双平台并行模拟')
    parser.add_argument(
        '--config', 
        type=str, 
        required=True,
        help='配置文件路径 (simulation_config.json)'
    )
    parser.add_argument(
        '--twitter-only',
        action='store_true',
        help='只运行Twitter模拟'
    )
    parser.add_argument(
        '--reddit-only',
        action='store_true',
        help='只运行Reddit模拟'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=40,
        help='最大模拟轮数（默认 40，用于截断过长的模拟；传 0/负数视为不限制）'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='模拟完成后立即关闭环境，不进入等待命令模式'
    )
    
    args = parser.parse_args()
    
    # 在 main 函数开始时创建 shutdown 事件，确保整个程序都能响应退出信号
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    if not os.path.exists(args.config):
        print(f"错误: 配置文件不存在: {args.config}")
        sys.exit(1)
    
    config = load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."
    wait_for_commands = not args.no_wait
    
    # 初始化日志配置（禁用 OASIS 日志，清理旧文件）
    init_logging_for_simulation(simulation_dir)
    
    # 创建日志管理器
    log_manager = SimulationLogManager(simulation_dir)
    twitter_logger = log_manager.get_twitter_logger()
    reddit_logger = log_manager.get_reddit_logger()
    
    log_manager.info("=" * 60)
    log_manager.info("OASIS 双平台并行模拟")
    log_manager.info(f"配置文件: {args.config}")
    log_manager.info(f"模拟ID: {config.get('simulation_id', 'unknown')}")
    log_manager.info(f"等待命令模式: {'启用' if wait_for_commands else '禁用'}")
    log_manager.info("=" * 60)
    
    time_config = config.get("time_config", {})
    total_hours = time_config.get('total_simulation_hours', 72)
    minutes_per_round = time_config.get('minutes_per_round', 60)
    config_total_rounds = (total_hours * 60) // minutes_per_round
    
    log_manager.info(f"模拟参数:")
    log_manager.info(f"  - 总模拟时长: {total_hours}小时")
    log_manager.info(f"  - 每轮时间: {minutes_per_round}分钟")
    log_manager.info(f"  - 配置总轮数: {config_total_rounds}")
    if args.max_rounds:
        log_manager.info(f"  - 最大轮数限制: {args.max_rounds}")
        if args.max_rounds < config_total_rounds:
            log_manager.info(f"  - 实际执行轮数: {args.max_rounds} (已截断)")
    log_manager.info(f"  - Agent数量: {len(config.get('agent_configs', []))}")

    # 加固：OASIS 按 profile 数组下标分配 agent_id，全链路（poster_agent_id / initial_follows /
    # agent_configs 查找）都假设 agent_configs[i].agent_id == i。此前无任何运行时校验——生成器
    # 一旦错位会表现为"语义错误但不崩溃"的模拟。这里在启动时做一次软校验，错位则显著告警（不中断）。
    _acfgs = config.get("agent_configs", []) or []
    _misaligned = [
        i for i, _c in enumerate(_acfgs)
        if isinstance(_c, dict) and str(_c.get("agent_id", i)) != str(i)
    ]
    if _misaligned:
        log_manager.warning(
            f"  - ⚠ agent_configs 下标与 agent_id 错位 {len(_misaligned)} 处"
            f"（首例 index={_misaligned[0]} agent_id={_acfgs[_misaligned[0]].get('agent_id')}）；"
            f"poster/follows/查找可能指向错误 persona——疑似生成器 bug，请检查。"
        )

    log_manager.info("日志结构:")
    log_manager.info(f"  - 主日志: simulation.log")
    log_manager.info(f"  - Twitter动作: twitter/actions.jsonl")
    log_manager.info(f"  - Reddit动作: reddit/actions.jsonl")
    log_manager.info("=" * 60)
    
    start_time = datetime.now()
    
    # 存储两个平台的模拟结果
    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None
    
    if args.twitter_only:
        twitter_result = await run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds)
    elif args.reddit_only:
        reddit_result = await run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds)
    else:
        # 并行运行（每个平台使用独立的日志记录器）
        # return_exceptions=True：一个平台抛异常不会取消另一个平台、也不会让 main() 崩溃；
        # 异常作为结果返回，下面单独降级为 None 并记录，保证平台之间相互隔离。
        results = await asyncio.gather(
            run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds,
                                   semaphore_platforms=2),
            run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds,
                                  semaphore_platforms=2),
            return_exceptions=True,
        )
        twitter_result, reddit_result = results
        if isinstance(twitter_result, BaseException):
            log_manager.error(f"Twitter 平台模拟异常（已隔离，不影响 Reddit）: {twitter_result}")
            twitter_result = None
        if isinstance(reddit_result, BaseException):
            log_manager.error(f"Reddit 平台模拟异常（已隔离，不影响 Twitter）: {reddit_result}")
            reddit_result = None
    
    total_elapsed = (datetime.now() - start_time).total_seconds()
    log_manager.info("=" * 60)
    log_manager.info(f"模拟循环完成! 总耗时: {total_elapsed:.1f}秒")

    # EXECPLAN2 I-2-0: 模拟结束后（只读）计算涌现结构 / 观点动力学度量。
    # 默认关闭（SIM_EMERGENT_METRICS!=true 时完全跳过，run 产物逐字节不变）；
    # 全程 try/except 隔离，失败不影响已完成的模拟与后续 interview/关闭流程。
    if os.environ.get("SIM_EMERGENT_METRICS", "false").strip().lower() == "true":
        emergent_platforms: List[str] = []
        if twitter_result is not None and twitter_result.env is not None:
            emergent_platforms.append("twitter")
        if reddit_result is not None and reddit_result.env is not None:
            emergent_platforms.append("reddit")
        if emergent_platforms:
            try:
                write_emergent_metrics(
                    simulation_dir, config, emergent_platforms, log_manager.info
                )
                log_manager.info("已生成涌现度量: emergent_metrics.json")
            except Exception as _em_err:  # noqa: BLE001
                log_manager.error(f"涌现度量计算失败（已隔离，不影响模拟结果）: {_em_err}")

    # NEXTSTEPS P1-1/P1-2/P1-4: 模拟结束后演化"结果世界态"——按轮 elicit 各活跃 agent 的承诺
    # （朝哪个 forecast 情景），资源加权步进 WorldState，落 world_state_trajectory.json +
    # decisions.jsonl + 终局 outcome（report/集成读它而非声量份额 final_stance_share）。
    # 默认关（SIM_DECISION_CHANNEL!=true 完全跳过，与现状一致）；全程 try/except 隔离。
    _ws_seed = config.get("world_state_seed") if isinstance(config, dict) else None
    if (os.environ.get("SIM_DECISION_CHANNEL", "false").strip().lower() == "true"
            and isinstance(_ws_seed, dict) and _ws_seed.get("scenarios")):
        try:
            from app.services.decision_channel import run_decision_channel
            from app.utils.llm_client import LLMClient
            from app.utils.atomic import write_json_atomic
            _acts = _read_actions_for_decision_channel(simulation_dir)
            try:
                _inertia = float(os.environ.get("SIM_DECISION_INERTIA", "0.7") or "0.7")
            except ValueError:
                _inertia = 0.7
            try:
                _eps = float(os.environ.get("SIM_CONVERGENCE_EPS", "0.02") or "0.02")
            except ValueError:
                _eps = 0.02
            _res = run_decision_channel(
                _acts, config.get("agent_configs"), _ws_seed, LLMClient(),
                inertia=_inertia, conv_eps=_eps,
                round_to_date=_build_round_to_date(_ws_seed, config),
            )
            if _res:
                write_json_atomic(
                    os.path.join(simulation_dir, "world_state_trajectory.json"), _res)
                with open(os.path.join(simulation_dir, "decisions.jsonl"), "w",
                          encoding="utf-8") as _df:
                    for _d in _res.get("decisions", []):
                        _df.write(json.dumps(_d, ensure_ascii=False) + "\n")
                _oc = _res.get("outcome", {})
                log_manager.info(
                    f"决策通道·结果世界态: leader={_oc.get('leader')} "
                    f"share={_oc.get('leader_share')} converged_at={_res.get('converged_at')}")
        except Exception as _dc_err:  # noqa: BLE001
            log_manager.error(f"决策通道演化失败（已隔离，不影响模拟结果）: {_dc_err}")

    # 是否进入等待命令模式
    if wait_for_commands:
        log_manager.info("")
        log_manager.info("=" * 60)
        log_manager.info("进入等待命令模式 - 环境保持运行")
        log_manager.info("支持的命令: interview, batch_interview, close_env")
        log_manager.info("=" * 60)
        
        # 创建IPC处理器
        ipc_handler = ParallelIPCHandler(
            simulation_dir=simulation_dir,
            twitter_env=twitter_result.env if twitter_result else None,
            twitter_agent_graph=twitter_result.agent_graph if twitter_result else None,
            reddit_env=reddit_result.env if reddit_result else None,
            reddit_agent_graph=reddit_result.agent_graph if reddit_result else None
        )
        ipc_handler.update_status("alive")
        
        # 等待命令循环（使用全局 _shutdown_event）
        try:
            while not _shutdown_event.is_set():
                should_continue = await ipc_handler.process_commands()
                if not should_continue:
                    break
                # 使用 wait_for 替代 sleep，这样可以响应 shutdown_event
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                    break  # 收到退出信号
                except asyncio.TimeoutError:
                    pass  # 超时继续循环
        except KeyboardInterrupt:
            print("\n收到中断信号")
        except asyncio.CancelledError:
            print("\n任务被取消")
        except Exception as e:
            print(f"\n命令处理出错: {e}")
        
        log_manager.info("\n关闭环境...")
        ipc_handler.update_status("stopped")
    
    # 关闭环境
    if twitter_result and twitter_result.env:
        await twitter_result.env.close()
        log_manager.info("[Twitter] 环境已关闭")
    
    if reddit_result and reddit_result.env:
        await reddit_result.env.close()
        log_manager.info("[Reddit] 环境已关闭")
    
    log_manager.info("=" * 60)
    log_manager.info(f"全部完成!")
    log_manager.info(f"日志文件:")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'simulation.log')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'twitter', 'actions.jsonl')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'reddit', 'actions.jsonl')}")
    log_manager.info("=" * 60)


def setup_signal_handlers(loop=None):
    """
    设置信号处理器，确保收到 SIGTERM/SIGINT 时能够正确退出
    
    持久化模拟场景：模拟完成后不退出，等待 interview 命令
    当收到终止信号时，需要：
    1. 通知 asyncio 循环退出等待
    2. 让程序有机会正常清理资源（关闭数据库、环境等）
    3. 然后才退出
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n收到 {sig_name} 信号，正在退出...")
        
        if not _cleanup_done:
            _cleanup_done = True
            # 设置事件通知 asyncio 循环退出（让循环有机会清理资源）
            if _shutdown_event:
                _shutdown_event.set()
        
        # 不要直接 sys.exit()，让 asyncio 循环正常退出并清理资源
        # 如果是重复收到信号，才强制退出
        else:
            print("强制退出...")
            sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被中断")
    except SystemExit:
        pass
    finally:
        # 清理 multiprocessing 资源跟踪器（防止退出时的警告）
        try:
            from multiprocessing import resource_tracker
            resource_tracker._resource_tracker._stop()
        except Exception:
            pass
        print("模拟进程已退出")
