"""共享 token 估算 / 上下文预算工具（EXECPLAN2 I-6-4）。

把原本散落在各调用点的硬编码字符切片（persona context[:3000]、前文章节[:8000]、
related_facts[:25] 等）统一为「按提供方上下文窗口动态计算」的预算化截断：
  - estimate_tokens(text)           —— 与 oasis_llm.CLIModel._estimate_tokens 一致的近似估算（ceil(len/4)）。
  - context_budget(provider, ...)   —— 由提供方上下文窗口扣除预留输出 + prompt 固定开销得到可用预算。
  - fit_to_budget(items, budget)    —— 贪婪地把上下文条目填入预算，单条硬上限，预算耗尽即停。
  - truncate_to_tokens(text, n)     —— 把单段文本按近似 token 上限截断（替代 text[:N] 字符切片）。

全部为纯函数 / 无副作用，且对 Config 旋钮的读取均用 getattr(..., default) 防御性兜底，
这样即便上层未配置（ADAPTIVE_CONTEXT 关闭）也能被安全 import / 调用。调用点应在
ADAPTIVE_CONTEXT 关闭时回退到原有硬编码切片（逐字节一致），开启时改用本模块。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, List, Optional

# 近似估算系数：≈4 字符/token（与 CLIModel._estimate_tokens、telemetry.estimate_tokens 同口径）。
_CHARS_PER_TOKEN = 4


def estimate_tokens(value: Any) -> int:
    """近似 token 数：ceil(len/4)。与 oasis_llm.CLIModel._estimate_tokens 保持一致口径。

    支持 str / list / dict（dict 走 json，list 递归求和），其余类型按其 str() 估算。
    空值返回 0。
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return max(1, math.ceil(len(value) / _CHARS_PER_TOKEN)) if value else 0
    if isinstance(value, (list, tuple)):
        return sum(estimate_tokens(item) for item in value)
    if isinstance(value, dict):
        import json
        return estimate_tokens(json.dumps(value, ensure_ascii=False))
    return estimate_tokens(str(value))


def context_window_for(provider: str) -> int:
    """某提供方的上下文窗口（token）。读 Config.context_window_for；缺配置时回退 32K。"""
    try:
        from ..config import Config
        return int(Config.context_window_for(provider))
    except Exception:
        return 32000


def context_budget(
    provider: str,
    *,
    model: Optional[str] = None,  # noqa: ARG001 — 预留：未来按具体模型细化窗口
    reserved_completion: Optional[int] = None,
    prompt_overhead: int = 0,
) -> int:
    """计算可用于「填充上下文」的 token 预算。

    usable = context_window(provider) - reserved_completion - prompt_overhead

    Args:
        provider: 提供方 id（openai/kimi/minimax/deepseek/qwen/glm/claude-cli/codex-cli）。
        model:    预留参数（当前按提供方粒度取窗口）。
        reserved_completion: 预留给输出生成的 token；缺省读 Config.RESERVED_COMPLETION_TOKENS。
        prompt_overhead: 模板 / 指令 / 系统提示等固定开销（已知时传入，从可用预算中扣除）。

    Returns:
        非负的可用上下文 token 预算（下限 0）。
    """
    window = context_window_for(provider)
    if reserved_completion is None:
        try:
            from ..config import Config
            reserved_completion = int(getattr(Config, "RESERVED_COMPLETION_TOKENS", 8192))
        except Exception:
            reserved_completion = 8192
    usable = window - int(reserved_completion) - int(max(0, prompt_overhead))
    return max(0, usable)


def _item_cap() -> int:
    """单条上下文条目允许占用的 token 硬上限（防单条吃光预算）。"""
    try:
        from ..config import Config
        return int(getattr(Config, "CONTEXT_ITEM_MAX_TOKENS", 4096))
    except Exception:
        return 4096


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """把单段文本近似截断到 max_tokens（按 4 字符/token 反推字符上限）。

    用于替代 text[:N] 字符硬切片：N(字符) ≈ max_tokens * 4。max_tokens<=0 返回空串。
    """
    if not text or max_tokens <= 0:
        return "" if max_tokens <= 0 else text
    char_limit = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= char_limit:
        return text
    return text[:char_limit]


def fit_to_budget(
    items: Iterable[Any],
    budget: int,
    *,
    item_cap: Optional[int] = None,
    to_text=str,
    sep_tokens: int = 1,
) -> List[str]:
    """贪婪地把上下文条目填入 token 预算，返回被纳入（必要时单条截断后）的文本列表。

    - 按原顺序遍历 items；累计 token 超过 budget 即停（已纳入的保留）。
    - 单条先按 item_cap（缺省 Config.CONTEXT_ITEM_MAX_TOKENS）硬截断，避免某条超长条目
      独吞整个预算；截断后若仍放不下当前剩余预算，则在剩余预算内进一步截断后纳入并结束。
    - sep_tokens：条目间分隔符的估算开销（如换行/项目符号），计入累计。

    与原有「取前 N 条 + 单条 [:M] 字符」相比，本函数让大窗口模型自然纳入更多条目、
    小窗口模型自动收紧，且永不超过预算。budget<=0 返回空列表。
    """
    if budget <= 0:
        return []
    cap = item_cap if item_cap is not None else _item_cap()
    out: List[str] = []
    used = 0
    for raw in items:
        text = to_text(raw)
        if not text:
            continue
        # 单条硬上限。
        text = truncate_to_tokens(text, cap)
        cost = estimate_tokens(text) + (sep_tokens if out else 0)
        if used + cost <= budget:
            out.append(text)
            used += cost
            continue
        # 放不下：在剩余预算内尽量纳入一段（避免「差一点点」就整条丢弃）。
        remaining = budget - used - (sep_tokens if out else 0)
        if remaining > 0:
            clipped = truncate_to_tokens(text, remaining)
            if clipped:
                out.append(clipped)
        break
    return out
