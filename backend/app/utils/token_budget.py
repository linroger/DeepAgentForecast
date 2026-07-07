"""
RQ-7 / EXECPLAN2 I-6-4：自适应上下文预算工具（token budgeting，纯函数、无副作用）。

动机（why）：报告/模拟里散落着大量硬编码字符切片（前序章节 [:8000]、人设上下文 [:3000]、
世界简报 ≤1400 字），它们对**所有**提供方一视同仁。但上下文窗口天差地别——MiniMax-M3 是
512K、DeepSeek 是 1M、而未知提供方回退只有 32K。固定小切片在大窗口模型上白白浪费了可用的
grounding 预算（把长研究档案切碎），在小窗口模型上又刚好合适。本模块把「固定切片」换成
「按提供方窗口动态计算的字符预算」：大窗口塞满、小窗口守住今天的 floor。

契约（invariants）：
- 纯函数：不读 Config、不做 I/O、不抛异常给调用方——任何异常内部吞掉并回退到安全值
  （floor / 原文），因此调用点可以无条件信任返回值（degrade-safe）。
- floor 语义：slice_budget_chars 永不返回小于 floor_chars 的值——小窗口/异常 → 今天的固定切片，
  保证「打开 ADAPTIVE_CONTEXT 只会放宽、绝不收紧」，与历史行为向后兼容。
- 估算器为近似（≈4 字符/token）：中英混排的真实 token 密度在 1.5~4 之间波动，故所有预算都应
  搭配充裕的 RESERVED_COMPLETION_TOKENS 安全余量使用（见 Config），避免顶满窗口后无处生成。

估算约定（≈4 字符/token）与 config.py ADAPTIVE_CONTEXT 注释一致；调用方可按需覆盖 chars_per_token。
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional

# ≈4 字符/token 的粗略换算（英文/代码偏保守，CJK 偏乐观；搭配 RESERVED_COMPLETION_TOKENS 兜底）。
CHARS_PER_TOKEN: float = 4.0


def _coerce_chars_per_token(chars_per_token) -> float:
    """把 chars_per_token 归一到一个正浮点；非法/<=0 → 回退默认，避免除零/负预算。"""
    try:
        cpt = float(chars_per_token)
    except (TypeError, ValueError):
        return CHARS_PER_TOKEN
    return cpt if cpt > 0 else CHARS_PER_TOKEN


def estimate_tokens(text, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """粗估一段文本的 token 数（向上取整）。空/None → 0；任何异常 → 0（保守，绝不抛出）。"""
    try:
        if not text:
            return 0
        cpt = _coerce_chars_per_token(chars_per_token)
        return int(math.ceil(len(str(text)) / cpt))
    except Exception:
        return 0


def truncate_to_tokens(text, max_tokens, chars_per_token: float = CHARS_PER_TOKEN) -> str:
    """把文本截断到至多 max_tokens（经 ≈chars_per_token 换算成字符上限）。

    max_tokens<=0 → 空串；已在预算内 → 原样返回；异常 → 原文（degrade-safe，绝不丢内容意外）。
    """
    try:
        s = "" if text is None else str(text)
        mt = int(max_tokens)
        if mt <= 0:
            return ""
        cpt = _coerce_chars_per_token(chars_per_token)
        max_chars = int(mt * cpt)
        if len(s) <= max_chars:
            return s
        return s[:max_chars]
    except Exception:
        return "" if text is None else str(text)


def context_budget(window_tokens, reserved_tokens, used_tokens=0) -> int:
    """可用于「上下文」的 token 预算 = 窗口 − 补全预留 − 已用；下限钳到 0。异常 → 0。"""
    try:
        return max(0, int(window_tokens) - int(reserved_tokens) - int(used_tokens))
    except Exception:
        return 0


def slice_budget_chars(
    *,
    window_tokens,
    reserved_tokens,
    floor_chars,
    share: float = 1.0,
    num_items: int = 1,
    chars_per_token: float = CHARS_PER_TOKEN,
    cap_tokens: Optional[int] = None,
) -> int:
    """RQ-7 主入口：把一个「固定字符切片」升级为「按提供方窗口预算化的字符上限」。

    计算：avail = context_budget(window, reserved)；可选 cap_tokens 再压顶；
    budget_chars = avail × share × chars_per_token ÷ num_items；返回 max(floor_chars, budget_chars)。

    参数语义：
    - floor_chars：今天的固定切片长度——返回值**永不低于**它（小窗口/异常都退回此值）。
    - share：本类上下文可占用窗口的比例（0~1）——前序章节要与系统提示/工具回包/补全共享窗口，
      故取 <1 的保守份额。
    - num_items：把 share 预算平摊到 N 个同类条目（如 N 个前序章节各自的字符上限），
      使「章节越多、每章允许越短」，避免 N×大切片撑爆窗口。
    - cap_tokens：对可用预算的硬上限（token），防止 1M 窗口给出天文数字的无意义切片。

    floor_chars 非法 → 0（无法给出更差的兜底）；其余任何异常 → floor_chars。
    """
    try:
        floor = int(floor_chars)
    except (TypeError, ValueError):
        return 0
    try:
        avail = context_budget(window_tokens, reserved_tokens)
        if avail <= 0:
            return floor
        if cap_tokens is not None:
            try:
                avail = min(avail, int(cap_tokens))
            except (TypeError, ValueError):
                pass
        n = max(1, int(num_items))
        cpt = _coerce_chars_per_token(chars_per_token)
        budget_chars = int(avail * float(share) * cpt / n)
        return max(floor, budget_chars)
    except Exception:
        return floor


def clamp_chars(value, floor_chars, ceiling_chars) -> int:
    """把一个字符数钳到 [floor, ceiling]。用于「1400→3000」这类既有下限又有硬上限的切片
    （大窗口封顶到 ceiling，小窗口守住 floor）。任何异常 → floor（保守）。"""
    try:
        v = int(value)
        lo = int(floor_chars)
        hi = int(ceiling_chars)
        if hi < lo:
            hi = lo
        return max(lo, min(hi, v))
    except Exception:
        try:
            return int(floor_chars)
        except (TypeError, ValueError):
            return 0


def fit_to_budget(
    items: Iterable,
    budget_tokens,
    item_max_tokens: Optional[int] = None,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> List[str]:
    """把一组条目按顺序塞进 token 预算：逐条截断（可选单条上限 item_max_tokens），
    预算耗尽即停。返回实际纳入（可能被截断）的字符串列表。

    用于「前序章节 / 事实清单」这类扇入：大窗口能纳入更多、更长的条目，小窗口自然只留前几条。
    预算非法 → 原样返回全部条目字符串（不做预算，degrade-safe）。
    """
    out: List[str] = []
    try:
        remaining = int(budget_tokens)
    except (TypeError, ValueError):
        return [("" if it is None else str(it)) for it in (items or [])]
    for it in (items or []):
        if remaining <= 0:
            break
        s = "" if it is None else str(it)
        if not s:
            continue
        if item_max_tokens is None:
            cap = remaining
        else:
            try:
                cap = min(remaining, int(item_max_tokens))
            except (TypeError, ValueError):
                cap = remaining
        piece = truncate_to_tokens(s, cap, chars_per_token)
        if piece:
            out.append(piece)
            remaining -= estimate_tokens(piece, chars_per_token)
    return out
