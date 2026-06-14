"""actors.json（DeerFlow 深度研究的结构化产物）的共享工具。

handoff 契约中的 actors.json 形如（NEW 字段均为可选，缺失即降级到旧行为）：

    {
      "central_question": "...",
      "as_of_date": "2026-06-08",
      "situation_brief": {                       // NEW —模拟可直接消费的局势简报
        "current_situation": "...", "context": "...", "dynamics": "...",
        "fault_lines": ["..."], "catalysts": ["..."]
      },
      "actors": [
        {"name": "...", "type": "Person|Organization|Media|Government|Platform|Other",
         "role": "...", "stance": "...", "influence": "high|medium|low",
         "memory": "该角色对事件的已知信息/立场记忆"}
      ],
      "relationships": [                         // NEW — 命名 actor 之间的有向、带类型边
        {"source": "...", "target": "...",       // source/target 必须 = 某个 actors[].name
         "type": "ALLY_OF|OPPOSES|COMPETES_WITH|REGULATES|DEPENDS_ON|PARTNERS_WITH|INFLUENCES",
         "sign": "ally|rival|neutral", "strength": "high|medium|low", "basis": "调研实证一行"}
      ],
      "key_events": [{"date": "...", "event": "..."}],
      "hot_topics": ["..."]
    }

本模块提供：
* ``match_actor``            — 把 Zep 实体名匹配回研究档案中的 actor（标准化精确匹配 → 双向包含）。
* ``actor_briefing``         — 单个 actor 的提示词注入块（persona / agent 配置生成用）。
* ``actors_digest``          — 全量 actors + key_events + hot_topics 的上下文摘要（配置生成用）。
* ``extract_relationship_rows`` — 过滤出 source/target 都能匹配到 actor 的关系行。
* ``situation_brief_block``  — 把 situation_brief 渲染为紧凑的中文提示块。
* ``relationship_briefing``  — 单个 actor 的社会关系网提示块（命名真实对手方）。
* ``build_initial_follow_graph`` — relationships[] → 模拟初始关注边 [[follower_id, followee_id]]。
* ``events_to_schedule``     — key_events → 映射到模拟轮次的事件计划。

设计原则：actors.json 永远是「可选增强」。任何字段缺失 / 类型不符都静默降级为
None / 空串 / 空列表，绝不让结构化数据的缺陷阻断原有的纯 LLM 生成路径。
所有下游消费者都只经由本模块读取 actors.json，绝不自行解析原始字典。
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


# ---------------------------------------------------------------------------
# 关系图 / 局势简报（EXECPLAN T1.3 — 黄金主线的基础工具）
# ---------------------------------------------------------------------------

# relationships[].type → 知识图谱边名（恒等映射，但集中在此以免散落各处漂移）
REL_EDGE_NAME = {
    "ALLY_OF": "ALLY_OF",
    "OPPOSES": "OPPOSES",
    "COMPETES_WITH": "COMPETES_WITH",
    "REGULATES": "REGULATES",
    "DEPENDS_ON": "DEPENDS_ON",
    "PARTNERS_WITH": "PARTNERS_WITH",
    "INFLUENCES": "INFLUENCES",
}

# 边类型 → 中文标签（persona 提示块用）
REL_LABEL = {
    "ALLY_OF": "盟友",
    "OPPOSES": "对立",
    "COMPETES_WITH": "竞争",
    "REGULATES": "监管",
    "DEPENDS_ON": "依赖",
    "PARTNERS_WITH": "伙伴",
    "INFLUENCES": "影响",
}


def extract_relationship_rows(actors: Optional[Any]) -> List[Dict[str, Any]]:
    """取出 source 与 target 都能标准化匹配到某个 actors[].name 的关系行。

    脏数据（非 dict、缺 source/target、端点不在 actor 表内）一律剔除；
    actors 不是 dict 或没有 relationships 时返回 []。
    """
    if not isinstance(actors, dict):
        return []
    rels = actors.get("relationships")
    if not isinstance(rels, list):
        return []
    rows = extract_actor_rows(actors)
    names = {normalize_name(r["name"]) for r in rows}
    out: List[Dict[str, Any]] = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        s, t = r.get("source"), r.get("target")
        if s and t and normalize_name(str(s)) in names and normalize_name(str(t)) in names:
            out.append(r)
    return out


def situation_brief_block(actors: Optional[Any]) -> str:
    """把 situation_brief 渲染为紧凑的中文提示块；缺失返回空串。"""
    sb = actors.get("situation_brief") if isinstance(actors, dict) else None
    if not isinstance(sb, dict):
        return ""
    parts: List[str] = []
    for label, key in (("当前态势", "current_situation"), ("来龙去脉", "context"), ("张力/动态", "dynamics")):
        v = str(sb.get(key, "") or "").strip()
        if v:
            parts.append(f"### {label}\n{v}")
    for label, key in (("争议断层", "fault_lines"), ("潜在触发", "catalysts")):
        lst = sb.get(key)
        if isinstance(lst, list) and lst:
            parts.append(f"### {label}\n" + "\n".join(f"- {x}" for x in lst[:6]))
    if not parts:
        return ""
    return "## 局势简报（深度研究实证，作为权威背景）\n" + "\n".join(parts)


def relationship_briefing(actor_name: str, actors: Optional[Any], max_edges: int = 6) -> str:
    """单个 actor 的社会关系网提示块，命名真实对手方；无关系返回空串。"""
    if not actor_name:
        return ""
    rows = extract_relationship_rows(actors)
    me = normalize_name(actor_name)
    out: List[str] = []
    for r in rows:
        typ = str(r.get("type", "")).upper()
        s, t = normalize_name(str(r.get("source", ""))), normalize_name(str(r.get("target", "")))
        strength = str(r.get("strength", "") or "")
        if me == s:
            out.append(f"{REL_LABEL.get(typ, '关联')}（{strength}）: {r.get('target')}")
        elif me == t:
            out.append(f"被{REL_LABEL.get(typ, '关联')}: {r.get('source')}")
        if len(out) >= max_edges:
            break
    if not out:
        return ""
    return "## 你的社会关系网（调研实证，互动时据此 @ 相关方）\n" + "\n".join("- " + x for x in out)


def build_initial_follow_graph(
    actors: Optional[Any],
    agent_id_by_name: Dict[str, int],
) -> List[List[int]]:
    """relationships[] → 去重后的初始关注边 [[follower_id, followee_id]]。

    ``agent_id_by_name`` 的键应已 ``normalize_name`` 标准化。关注方向遵循 EXECPLAN §2
    的方向表。端点无法解析 / 自环一律跳过；空数据返回 []。
    """
    rows = extract_relationship_rows(actors)
    pairs: set = set()

    def aid(n: Any) -> Optional[int]:
        return agent_id_by_name.get(normalize_name(str(n)))

    for r in rows:
        s, d = aid(r.get("source")), aid(r.get("target"))
        typ = str(r.get("type", "")).upper()
        if s is None or d is None or s == d:
            continue
        si = influence_weight(match_actor(str(r.get("source", "")), actors)) or 1.0
        ti = influence_weight(match_actor(str(r.get("target", "")), actors)) or 1.0
        if typ == "ALLY_OF":
            # 低影响力 → 关注高影响力
            pairs.add((s, d) if si <= ti else (d, s))
        elif typ in ("PARTNERS_WITH", "OPPOSES", "COMPETES_WITH"):
            pairs.add((s, d))
            pairs.add((d, s))
        elif typ == "DEPENDS_ON":
            pairs.add((s, d))           # 依赖方关注被依赖方
        elif typ == "INFLUENCES":
            pairs.add((d, s))           # 受众关注影响者
        elif typ == "REGULATES":
            pairs.add((s, d))           # 监管方关注被监管方（监控）
    return [list(p) for p in pairs]


def situation_brief(actors: Optional[Any], max_actors: int = 25, max_chars: int = 6000) -> str:
    """报告用「背景档案」整体渲染（EXECPLAN T4.1）。

    把研究档案压成一段权威背景，钉进 ReportAgent 的提示词，省去报告阶段对全套
    cast/关系/时间线的盲搜：central_question + as_of_date + 局势简报 + 角色表 +
    角色关系 + 关键时间线 + 热点。空档案返回空串。
    """
    if not isinstance(actors, dict):
        return ""
    lines: List[str] = []
    cq = str(actors.get("central_question", "") or "").strip()
    aod = str(actors.get("as_of_date", "") or "").strip()
    if cq:
        lines.append(f"**核心问题**：{cq}")
    if aod:
        lines.append(f"**研究截止日 (as-of)**：{aod}")

    sb = situation_brief_block(actors)
    if sb:
        lines.append(sb)

    rows = extract_actor_rows(actors)
    if rows:
        lines.append("## 关键角色（调研实证）")
        for r in rows[:max_actors]:
            name = str(r.get("name", "?"))
            typ = str(r.get("type", "") or "")
            role = str(r.get("role", "") or "")
            stance = str(r.get("stance", "") or "")
            infl = str(r.get("influence", "") or "")
            seg = f"- {name}（{typ}）"
            if role:
                seg += f" 角色: {role}"
            if stance:
                seg += f" | 立场: {stance}"
            if infl:
                seg += f" | 影响力: {infl}"
            lines.append(seg)

    rels = extract_relationship_rows(actors)
    if rels:
        lines.append("## 角色关系（有向，调研实证）")
        for r in rels[:40]:
            typ = str(r.get("type", "")).upper()
            label = REL_LABEL.get(typ, typ or "关联")
            strength = str(r.get("strength", "") or "")
            basis = str(r.get("basis", "") or "")
            seg = f"- {r.get('source')} --[{label}]--> {r.get('target')}"
            if strength:
                seg += f"（{strength}）"
            if basis:
                seg += f": {basis}"
            lines.append(seg)

    events = actors.get("key_events")
    if isinstance(events, list) and events:
        lines.append("## 关键时间线（调研实证）")
        for ev in events[:20]:
            if isinstance(ev, dict):
                date = str(ev.get("date", "") or "")
                desc = str(ev.get("event", "") or "")
                if desc:
                    lines.append(f"- {date} {desc}".strip())

    topics = actors.get("hot_topics")
    if isinstance(topics, list) and topics:
        lines.append("## 热点议题（调研实证）")
        lines.append("、".join(str(t) for t in topics[:15]))

    brief = "\n".join(lines)
    if len(brief) > max_chars:
        brief = brief[:max_chars] + "\n…(背景档案已截断)"
    return brief


def events_to_schedule(
    actors: Optional[Any],
    total_rounds: int,
    as_of_date: Optional[str],
    horizon_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """key_events → [{round, event, date}]，映射到 [0, total_rounds)。早于 as_of 的事件跳过。

    ``horizon_days`` 缺省时取「最远未来事件距 as_of 的天数」为视界。as_of 不可解析 / 无事件
    时返回 []（永不抛异常）。
    """
    from .dates import parse_as_of  # T2.3 helper（同包相对导入，匹配代码库约定）

    if not isinstance(actors, dict) or total_rounds <= 0:
        return []
    base = parse_as_of(as_of_date)
    if base is None:
        return []
    evs = actors.get("key_events") or []
    if not isinstance(evs, list):
        return []

    spans: List[int] = []
    for e in evs:
        if not isinstance(e, dict):
            continue
        d = parse_as_of(e.get("date"))
        if d is None:
            continue
        span = (d - base).days
        if span >= 0:
            spans.append(span)
    hz = horizon_days or (max(spans) if spans else 1) or 1

    out: List[Dict[str, Any]] = []
    for e in evs:
        if not isinstance(e, dict):
            continue
        d = parse_as_of(e.get("date"))
        if d is None:
            continue
        span = (d - base).days
        if span < 0:
            continue
        out.append({
            "round": min(total_rounds - 1, round(span / hz * total_rounds)),
            "event": e.get("event"),
            "date": e.get("date"),
        })
    return out
