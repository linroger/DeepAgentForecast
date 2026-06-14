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
      "hot_topics": ["..."],

      // EXECPLAN2 I-0-0：来源分层 + Admiralty 式定级（均为可选）。
      "sources": [
        {"title": "...", "url": "...",
         "tier": "S1|S2|S3|S4",            // S1=一手/权威 … S4=低可信
         "date": "2026-05-01",            // 该来源的发布/更新日
         "supports": ["claim-ref", ...],  // 该来源支撑的短声明引用
         "independent": true}              // 是否独立信源（非循环引用）
      ],
      // actor/relationship 行可带可选 "grade": "A1".."F6"（Admiralty 可靠度+可信度）。

      // EXECPLAN2 I-0-5：结构化定量事实表（每条带单位 + as-of 日 + 定义）。
      "quantitative_facts": [
        {"metric": "...", "value": "...", "unit": "...",
         "as_of_date": "2026-05-01", "definition": "...",
         "source": "...", "tier": "S1|S2|S3|S4"}
      ],

      // EXECPLAN2 I-0-1：证据冲突 / 争议声明（triangulation / ACH-lite 的结构化产物）。
      "contested_claims": [
        {"claim": "...",
         "positions": [{"stance": "...", "sources": ["..."], "tier": "S1|S2|S3|S4"}],
         "status": "confirmed|contested|speculative|single-origin",
         "why_they_differ": "..."}
      ],

      // EXECPLAN2 I-0-2：预测输入（外部视角基率 / 驱动变量 / 可观测指标 / 情景）。
      "forecast_inputs": {
        "base_rates": [{"reference_class": "...", "outcome_frequency": "...", "basis": "..."}],
        "drivers": [{"variable": "...", "direction": "...", "why_it_matters": "..."}],
        "indicators": [{"indicator": "...", "signals_what": "...", "date_or_trigger": "..."}],
        "scenarios": [{"name": "base|upside|downside", "probability_band": "...",
                       "narrative": "...", "key_assumptions": ["..."]}]
      }
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
* ``sources_index_tiered``   — I-0-0：按 S1-S4 分层渲染可引用来源索引（带日期/独立性/定级）。
* ``source_tier_histogram``  — I-0-0：统计 s1_count..s4_count（meta 观测用）。
* ``quantitative_facts_block`` / ``extract_quantitative_rows`` — I-0-5：定量事实表渲染 / 安全抽取。
* ``contested_claims_block`` / ``extract_contested_rows``      — I-0-1：争议证据块渲染 / 安全抽取。
* ``forecast_inputs_block`` / ``extract_forecast_inputs``      — I-0-2：预测输入块渲染 / 安全抽取。

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


# ---------------------------------------------------------------------------
# 来源分层 + 证据定级（EXECPLAN2 I-0-0）
# ---------------------------------------------------------------------------

# 规范化的来源层级顺序（S1=最强证据 … S4=最弱）。其余值归入「未分层」。
SOURCE_TIERS = ("S1", "S2", "S3", "S4")
# 层级 → 中文释义（索引头部说明用）
_TIER_DESC = {
    "S1": "一手/权威",
    "S2": "可靠二手",
    "S3": "一般媒体",
    "S4": "低可信/未证实",
}


def _norm_tier(value: Any) -> str:
    """把自由文本的 tier 归一化为 S1..S4；无法识别返回空串（→「未分层」）。"""
    s = str(value or "").strip().upper()
    if not s:
        return ""
    # 容忍 "S1"/"s1"/"tier 1"/"1"/"S1（一手）" 等写法
    m = re.search(r"S?\s*([1-4])", s)
    if m:
        return "S" + m.group(1)
    return ""


def extract_source_rows(sources: Optional[Any]) -> List[Dict[str, Any]]:
    """从来源容器安全取出来源行列表（容错任意脏数据）。

    ``sources`` 可为：sources.json 的顶层 list、或 actors.json 里嵌套的
    ``{"sources": [...]}``。非法形状一律返回 []，绝不抛异常。
    """
    rows: Any = sources
    if isinstance(sources, dict):
        rows = sources.get("sources")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and (r.get("title") or r.get("url"))]


def source_tier_histogram(sources: Optional[Any]) -> Dict[str, int]:
    """统计各层级来源数量 → {s1_count, s2_count, s3_count, s4_count, untiered_count}。

    供 meta.json 观测：让覆盖率门可据此拒绝「全是 S4」的报告。空数据返回全 0。
    """
    hist = {"s1_count": 0, "s2_count": 0, "s3_count": 0, "s4_count": 0, "untiered_count": 0}
    for r in extract_source_rows(sources):
        tier = _norm_tier(r.get("tier"))
        if tier:
            hist[tier.lower() + "_count"] += 1
        else:
            hist["untiered_count"] += 1
    return hist


def sources_index_tiered(sources: Optional[Any], max_sources: int = 40) -> str:
    """按 S1-S4 分层渲染可引用来源索引；缺省/空数据返回空串（→ 调用方回退旧逻辑）。

    引用记号形如 ``[S1-a]`` / ``[S2-b]``（层级 + 该层内字母序），并附日期与独立性，
    让报告代理优先引用 S1/S2 证据、并能在正文按真实层级标注，而不是按位置伪造 [S1]。
    """
    rows = extract_source_rows(sources)
    if not rows:
        return ""

    # 分桶：S1..S4 按序，未分层归入末尾的 "S?" 桶
    buckets: Dict[str, List[Dict[str, Any]]] = {t: [] for t in SOURCE_TIERS}
    buckets["S?"] = []
    used = 0
    for r in rows:
        if used >= max_sources:
            break
        tier = _norm_tier(r.get("tier")) or "S?"
        buckets[tier].append(r)
        used += 1

    lines = ["【可引用来源（按证据层级；正文用 [S1-a]/[S2-b] 形式标注，优先引用高层级）】"]
    letters = "abcdefghijklmnopqrstuvwxyz"
    for tier in (*SOURCE_TIERS, "S?"):
        items = buckets.get(tier) or []
        if not items:
            continue
        if tier == "S?":
            lines.append("— 未分层来源")
        else:
            lines.append(f"— {tier}（{_TIER_DESC.get(tier, '')}）")
        for i, s in enumerate(items):
            tag = letters[i] if i < len(letters) else str(i)
            ref = f"[{tier}-{tag}]"
            title = str(s.get("title", "") or "").strip()
            url = str(s.get("url", "") or "").strip()
            date = str(s.get("date", "") or "").strip()
            seg = f"{ref} {title}".rstrip()
            extras: List[str] = []
            if date:
                extras.append(date)
            if s.get("independent") is False:
                extras.append("非独立")
            if extras:
                seg += f"（{'，'.join(extras)}）"
            if url:
                seg += f" — {url}"
            lines.append(seg)
    return "\n".join(lines) if len(lines) > 1 else ""


def actor_grade(actor: Optional[Dict[str, Any]]) -> str:
    """取出 actor/relationship 行上的可选 Admiralty 定级（如 "B2"）；缺失返回空串。"""
    if not isinstance(actor, dict):
        return ""
    g = str(actor.get("grade", "") or "").strip().upper()
    # 容错：仅接受形如「字母+数字」的 Admiralty 记号，其余忽略
    return g if re.fullmatch(r"[A-F][1-6]", g) else ""


# ---------------------------------------------------------------------------
# 结构化定量事实表（EXECPLAN2 I-0-5）
# ---------------------------------------------------------------------------


def extract_quantitative_rows(actors: Optional[Any]) -> List[Dict[str, Any]]:
    """从 actors.json 安全取出 quantitative_facts 行（需至少有 metric 与 value）。"""
    if not isinstance(actors, dict):
        return []
    rows = actors.get("quantitative_facts")
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict) and str(r.get("metric", "") or "").strip() and \
                str(r.get("value", "") or "").strip():
            out.append(r)
    return out


def quantitative_facts_block(actors: Optional[Any], max_facts: int = 20) -> str:
    """把 quantitative_facts 渲染为紧凑的中文 markdown 表；缺省返回空串。

    每行携带单位 + as-of 日 + 定义 + 来源/层级，报告代理可直接引用精确、带日期、
    有定义的数字，无需再次联网检索，亦避免 SKILL §6 警示的「定义漂移」。
    """
    rows = extract_quantitative_rows(actors)
    if not rows:
        return ""
    rows = rows[:max_facts]
    lines = ["## 定量事实（深度研究实证，引用时务必带单位与 as-of 日）",
             "| 指标 | 数值 | 单位 | as-of | 定义 | 来源 |",
             "| --- | --- | --- | --- | --- | --- |"]

    def cell(v: Any) -> str:
        # markdown 表格安全：转义竖线、压平换行
        return str(v or "").replace("|", "\\|").replace("\n", " ").strip()

    for r in rows:
        src = cell(r.get("source"))
        tier = _norm_tier(r.get("tier"))
        if tier and src:
            src = f"{src}（{tier}）"
        elif tier:
            src = tier
        lines.append(
            "| {metric} | {value} | {unit} | {as_of} | {definition} | {source} |".format(
                metric=cell(r.get("metric")) or "?",
                value=cell(r.get("value")),
                unit=cell(r.get("unit")),
                as_of=cell(r.get("as_of_date")),
                definition=cell(r.get("definition")),
                source=src,
            )
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 争议证据 / 证据冲突（EXECPLAN2 I-0-1）
# ---------------------------------------------------------------------------

# 合法的争议状态枚举；其余值在渲染时原样保留但不参与白名单。
CONTESTED_STATUS = ("confirmed", "contested", "speculative", "single-origin")
_STATUS_LABEL = {
    "confirmed": "已证实",
    "contested": "存在争议",
    "speculative": "推测性",
    "single-origin": "单一信源",
}


def extract_contested_rows(actors: Optional[Any]) -> List[Dict[str, Any]]:
    """安全取出 contested_claims 行（需有非空 claim）。

    每条要么 positions 含 >=2 个立场，要么 status 标为 single-origin，否则视为
    噪声（避免模型把琐碎分歧也列为争议）。形状不符一律剔除。
    """
    if not isinstance(actors, dict):
        return []
    rows = actors.get("contested_claims")
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        claim = str(r.get("claim", "") or "").strip()
        if not claim:
            continue
        positions = r.get("positions")
        positions = [p for p in positions if isinstance(p, dict)] if isinstance(positions, list) else []
        status = str(r.get("status", "") or "").strip().lower()
        if len(positions) >= 2 or status == "single-origin":
            out.append(r)
    return out


def contested_claims_block(actors: Optional[Any], max_claims: int = 8) -> str:
    """把 contested_claims 渲染为「争议证据」块；缺省返回空串。

    报告呈现「冲突而非平均」、模拟可据此给 agent 植入真实对立信念。
    """
    rows = extract_contested_rows(actors)
    if not rows:
        return ""
    rows = rows[:max_claims]
    out: List[str] = ["## 争议证据（深度研究：冲突而非平均，撰写时显式呈现分歧及缘由）"]
    for r in rows:
        claim = str(r.get("claim", "") or "").strip()
        status = str(r.get("status", "") or "").strip().lower()
        label = _STATUS_LABEL.get(status, status or "")
        head = f"- 声明：{claim}"
        if label:
            head += f"（{label}）"
        out.append(head)
        positions = r.get("positions")
        if isinstance(positions, list):
            for p in positions:
                if not isinstance(p, dict):
                    continue
                stance = str(p.get("stance", "") or "").strip()
                if not stance:
                    continue
                srcs = p.get("sources")
                srcs = [str(x) for x in srcs if str(x or "").strip()] if isinstance(srcs, list) else []
                tier = _norm_tier(p.get("tier"))
                seg = f"  - 立场：{stance}"
                tags: List[str] = []
                if tier:
                    tags.append(tier)
                if srcs:
                    tags.append("、".join(srcs[:3]))
                if tags:
                    seg += f"（{' / '.join(tags)}）"
                out.append(seg)
        why = str(r.get("why_they_differ", "") or "").strip()
        if why:
            out.append(f"  - 分歧缘由：{why}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 预测输入：基率 / 驱动变量 / 可观测指标 / 情景（EXECPLAN2 I-0-2）
# ---------------------------------------------------------------------------

# 合法情景名（其余原样保留）
SCENARIO_NAMES = ("base", "upside", "downside")
_SCENARIO_LABEL = {"base": "基线", "upside": "上行", "downside": "下行"}


def extract_forecast_inputs(actors: Optional[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """安全取出 forecast_inputs 的四类列表 → {base_rates, drivers, indicators, scenarios}。

    任一子键缺失/非法均降级为空列表；整体缺失返回四个空列表。
    """
    empty = {"base_rates": [], "drivers": [], "indicators": [], "scenarios": []}
    if not isinstance(actors, dict):
        return empty
    fi = actors.get("forecast_inputs")
    if not isinstance(fi, dict):
        return empty
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key in ("base_rates", "drivers", "indicators", "scenarios"):
        lst = fi.get(key)
        out[key] = [x for x in lst if isinstance(x, dict)] if isinstance(lst, list) else []
    return out


def forecast_inputs_block(actors: Optional[Any], max_per_section: int = 6) -> str:
    """把 forecast_inputs 渲染为预测脚手架块；全空返回空串。

    给报告代理一个外部视角基率 + 驱动 + 带日期指标 + 概率区间情景的校准底座，
    让预测可证伪（指标）、可校准（情景概率），而非纯叙事。
    """
    fi = extract_forecast_inputs(actors)
    base_rates = fi["base_rates"][:max_per_section]
    drivers = fi["drivers"][:max_per_section]
    indicators = fi["indicators"][:max_per_section]
    scenarios = fi["scenarios"][:max_per_section]
    if not (base_rates or drivers or indicators or scenarios):
        return ""

    out: List[str] = ["## 预测输入（深度研究实证；作为校准脚手架，撰写时据此给出概率与可观测信号）"]

    if base_rates:
        out.append("### 外部视角基率（参照类）")
        for b in base_rates:
            rc = str(b.get("reference_class", "") or "").strip()
            freq = str(b.get("outcome_frequency", "") or "").strip()
            basis = str(b.get("basis", "") or "").strip()
            if not rc:
                continue
            seg = f"- {rc}"
            if freq:
                seg += f"：基率 {freq}"
            if basis:
                seg += f"（依据：{basis}）"
            out.append(seg)

    if drivers:
        out.append("### 关键驱动变量")
        for d in drivers:
            var = str(d.get("variable", "") or "").strip()
            direction = str(d.get("direction", "") or "").strip()
            why = str(d.get("why_it_matters", "") or "").strip()
            if not var:
                continue
            seg = f"- {var}"
            if direction:
                seg += f"（方向：{direction}）"
            if why:
                seg += f"：{why}"
            out.append(seg)

    if indicators:
        out.append("### 可观测指标（带日期/触发条件，用于事后验证）")
        for ind in indicators:
            name = str(ind.get("indicator", "") or "").strip()
            signals = str(ind.get("signals_what", "") or "").strip()
            when = str(ind.get("date_or_trigger", "") or "").strip()
            if not name:
                continue
            seg = f"- {name}"
            if when:
                seg += f"（{when}）"
            if signals:
                seg += f"：预示 {signals}"
            out.append(seg)

    if scenarios:
        out.append("### 情景（基线/上行/下行 + 概率区间）")
        for sc in scenarios:
            raw = str(sc.get("name", "") or "").strip().lower()
            label = _SCENARIO_LABEL.get(raw, raw or "情景")
            band = str(sc.get("probability_band", "") or "").strip()
            narrative = str(sc.get("narrative", "") or "").strip()
            seg = f"- {label}"
            if band:
                seg += f"（概率 {band}）"
            if narrative:
                seg += f"：{narrative}"
            out.append(seg)
            assumptions = sc.get("key_assumptions")
            if isinstance(assumptions, list):
                for a in [str(x) for x in assumptions if str(x or "").strip()][:4]:
                    out.append(f"  - 关键假设：{a}")
    return "\n".join(out)


def indicators_to_schedule(
    actors: Optional[Any],
    total_rounds: int,
    as_of_date: Optional[str],
    horizon_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """forecast_inputs.indicators 中带日期者 → [{round, indicator, date}]，复用 key_events 的映射逻辑。

    只挑选 ``date_or_trigger`` 能被 ``parse_as_of`` 解析为未来日期的指标，把它们落到对应
    模拟轮次，让模拟可在「该信号本应出现」的时点关注对应驱动变量。无可解析项返回 []。
    """
    from .dates import parse_as_of  # 同包相对导入，匹配代码库约定

    if not isinstance(actors, dict) or total_rounds <= 0:
        return []
    base = parse_as_of(as_of_date)
    if base is None:
        return []
    inds = extract_forecast_inputs(actors)["indicators"]
    if not inds:
        return []

    spans: List[int] = []
    for ind in inds:
        d = parse_as_of(ind.get("date_or_trigger"))
        if d is None:
            continue
        span = (d - base).days
        if span >= 0:
            spans.append(span)
    hz = horizon_days or (max(spans) if spans else 1) or 1

    out: List[Dict[str, Any]] = []
    for ind in inds:
        d = parse_as_of(ind.get("date_or_trigger"))
        if d is None:
            continue
        span = (d - base).days
        if span < 0:
            continue
        out.append({
            "round": min(total_rounds - 1, round(span / hz * total_rounds)),
            "indicator": ind.get("indicator"),
            "date": ind.get("date_or_trigger"),
        })
    return out
