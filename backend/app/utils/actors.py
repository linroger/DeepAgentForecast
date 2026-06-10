"""actors.json（DeerFlow 深度研究的结构化产物）的共享工具。

handoff 契约中的 actors.json 形如：

    {
      "central_question": "...",
      "as_of_date": "2026-06-08",
      "actors": [
        {"name": "...", "type": "Person|Organization|Media|Government|Platform",
         "role": "...", "stance": "...", "influence": "high|medium|low",
         "memory": "该角色对事件的已知信息/立场记忆"}
      ],
      "key_events": [{"date": "...", "event": "..."}],
      "hot_topics": ["..."]
    }

本模块提供：
* ``match_actor``      — 把 Zep 实体名匹配回研究档案中的 actor（标准化精确匹配 → 双向包含）。
* ``actor_briefing``   — 单个 actor 的提示词注入块（persona / agent 配置生成用）。
* ``actors_digest``    — 全量 actors + key_events + hot_topics 的上下文摘要（配置生成用）。

设计原则：actors.json 永远是「可选增强」。任何字段缺失 / 类型不符都静默降级为
None / 空串，绝不让结构化数据的缺陷阻断原有的纯 LLM 生成路径。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

# influence 的自由文本 → 数值权重（与 simulation_config 的 influence_weight 同标度）
INFLUENCE_WEIGHTS = {
    "high": 2.5,
    "高": 2.5,
    "medium": 1.5,
    "med": 1.5,
    "中": 1.5,
    "low": 1.0,
    "低": 1.0,
}


def normalize_name(name: str) -> str:
    """实体名标准化：全半角统一、去空白/标点、小写。

    Zep 抽取的实体名（如 "OpenAI 公司"）与 DeerFlow 写出的 actor 名（如 "OpenAI"）
    经常只差大小写 / 空格 / 公司后缀，标准化后做包含匹配能覆盖绝大多数情况。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).lower()
    # 去掉所有空白与常见标点（中英文），保留字母数字与 CJK
    s = re.sub(r"[\s\.\,\:\;\-\_\(\)\[\]【】（）'\"·]+", "", s)
    return s


def extract_actor_rows(actors: Optional[Any]) -> List[Dict[str, Any]]:
    """从 actors.json 顶层对象安全取出 actor 行列表（容错任意脏数据）。"""
    if not isinstance(actors, dict):
        return []
    rows = actors.get("actors")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("name")]


def match_actor(entity_name: str, actors: Optional[Any]) -> Optional[Dict[str, Any]]:
    """把一个 Zep 实体名匹配回研究档案的 actor 行。

    匹配顺序：标准化精确匹配 → 双向包含（取较长名者优先，避免 "AI" 这类
    短名误配）。无匹配返回 None。
    """
    rows = extract_actor_rows(actors)
    if not rows or not entity_name:
        return None
    target = normalize_name(entity_name)
    if not target:
        return None

    best: Optional[Dict[str, Any]] = None
    best_len = 0
    for row in rows:
        cand = normalize_name(str(row.get("name", "")))
        if not cand:
            continue
        if cand == target:
            return row
        # 双向包含；要求重叠名至少 2 个字符，否则噪声太大
        if len(cand) >= 2 and (cand in target or target in cand):
            if len(cand) > best_len:
                best, best_len = row, len(cand)
    return best


def influence_weight(actor: Optional[Dict[str, Any]]) -> Optional[float]:
    """actor.influence（high/medium/low 等自由文本）→ 数值权重；无法解析返回 None。"""
    if not isinstance(actor, dict):
        return None
    raw = str(actor.get("influence", "")).strip().lower()
    if not raw:
        return None
    for key, weight in INFLUENCE_WEIGHTS.items():
        if key in raw:
            return weight
    return None


def actor_briefing(actor: Optional[Dict[str, Any]], max_memory_chars: int = 600) -> str:
    """单个 actor 的提示词注入块（空 actor 返回空串）。

    persona / agent 配置生成的提示词里以「研究实证」的口吻注入，引导 LLM
    以真实调研数据为准，而不是凭报告行文再猜一遍。
    """
    if not isinstance(actor, dict):
        return ""
    parts: List[str] = []
    for label, key in (("角色定位", "role"), ("立场", "stance"), ("影响力", "influence")):
        val = str(actor.get(key, "") or "").strip()
        if val:
            parts.append(f"- {label}: {val}")
    memory = str(actor.get("memory", "") or "").strip()
    if memory:
        if len(memory) > max_memory_chars:
            memory = memory[:max_memory_chars] + "…"
        parts.append(f"- 已知事实/记忆: {memory}")
    if not parts:
        return ""
    return (
        "## 深度研究实证档案（来自真实网络调研，生成时必须以此为准）\n"
        + "\n".join(parts)
    )


def actors_digest(
    actors: Optional[Any],
    max_actors: int = 30,
    max_chars: int = 4000,
) -> str:
    """全量研究档案摘要：actors 表 + key_events + hot_topics。

    供 SimulationConfigGenerator 的上下文与事件配置提示词使用。超长时按行截断。
    """
    if not isinstance(actors, dict):
        return ""
    lines: List[str] = []

    rows = extract_actor_rows(actors)
    if rows:
        lines.append("### 深度研究确认的真实角色（立场/影响力为调研实证）")
        for row in rows[:max_actors]:
            name = str(row.get("name", "?"))
            typ = str(row.get("type", "") or "")
            role = str(row.get("role", "") or "")
            stance = str(row.get("stance", "") or "")
            influence = str(row.get("influence", "") or "")
            seg = f"- {name}（{typ}）"
            if role:
                seg += f" 角色: {role}"
            if stance:
                seg += f" | 立场: {stance}"
            if influence:
                seg += f" | 影响力: {influence}"
            lines.append(seg)

    events = actors.get("key_events")
    if isinstance(events, list) and events:
        lines.append("### 关键时间线（调研实证）")
        for ev in events[:15]:
            if isinstance(ev, dict):
                date = str(ev.get("date", "") or "")
                desc = str(ev.get("event", "") or "")
                if desc:
                    lines.append(f"- {date} {desc}".strip())

    topics = actors.get("hot_topics")
    if isinstance(topics, list) and topics:
        lines.append("### 调研发现的热点议题")
        lines.append("、".join(str(t) for t in topics[:12]))

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars] + "\n…(研究档案已截断)"
    return digest
