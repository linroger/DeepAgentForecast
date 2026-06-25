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
         // NEW 身份消歧 / 别名解析（KG cookbook：描述用于消歧，别名解析零字符重叠的同义名）：
         "description": "一句话锚定 who/what（消歧用）", "aliases": ["同义名/缩写/外文名"],
         // NEW 角色内在动机（actors-and-incentives 结构化，均可选；缺失即降级）：
         "goals": ["..."], "constraints": ["..."], "assets": ["..."],
         "vulnerabilities": ["..."], "stated_vs_revealed": "言行差异",
         "memory": "该角色对事件的已知信息/立场记忆",
         // NEW 本体分类（ONTOLOGY：区分「能动 agent」与「报道者/概念/资源」，均可选）：
         "archetype": "actor|collective|institution_rule|asset_object|event|signal|claim_narrative|constraint_resource|place_jurisdiction|source|scenario",
         "simulation_tier": 1,                    // 1=核心决策者 2=利益相关方 3=被动信息源 4=抽象概念/资源
         "role_class": "principal|arbiter|stakeholder|amplifier|intermediary",
         "salience": {"score": 0.85, "tier": "high|medium|low", "basis": "..."},
         // NEW 行为 DNA（持久人格底座；缺失即降级到旧的 stance/influence 标签）：
         "worldview": {"values": ["..."], "beliefs": ["..."], "identity": "...", "frame": "..."},
         "incentives": [{"driver": "...", "gains_if": "...", "loses_if": "...", "intensity": "high|medium|low"}],
         "resources": ["..."],                    // assets 的超集/别名
         "risk_tolerance": "low|medium|high"}
      ],
      "relationships": [                         // NEW — 命名 actor 之间的有向、带类型边
        {"source": "...", "target": "...",       // source/target 必须 = 某个 actors[].name
         // 8 个旧类型保留；NEW 经济/治理/媒体类型扩展（见 REL_EDGE_NAME）：
         "type": "ALLY_OF|OPPOSES|COMPETES_WITH|REGULATES|DEPENDS_ON|PARTNERS_WITH|INFLUENCES|OTHER"
                 "|SUPPLIES|CUSTOMER_OF|FUNDS|INVESTS_IN|BACKS|OWNS|SUPPORTS|SANCTIONS"
                 "|REPORTS_ON|CONSUMES|ENDORSES|CRITICIZES|LITIGATES_AGAINST",
         "relation_label": "type==OTHER 时的自由文本标签，如 SUPPLIES/FUNDS/OWNS",  // NEW 可选
         "valence": "allied|adversarial|neutral|transactional|directional",  // NEW 可选；缺失从 type 推
         "polarity": 0.4,                         // NEW 可选 -1..1；缺失从 valence/type 推
         "sign": "ally|rival|neutral", "strength": "high|medium|low",
         "since": "YYYY-MM-DD", "until": "YYYY-MM-DD",  // NEW 可选起止日
         "basis": "调研实证一行"}
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
* ``entity_archetype`` / ``entity_simulation_tier`` / ``is_agent_eligible`` — ONTOLOGY：实体分类与「能动 agent」准入门。
* ``salience_score``         — ONTOLOGY：actor 的显著度评分（salience.score → influence 回退）。
* ``relation_valence`` / ``relation_polarity`` — ONTOLOGY：关系边的价（allied/adversarial/…）与极性（-1..1）。
* ``relational_roster`` / ``roster_block`` — ONTOLOGY：把 relationships[] 投影为 10 个命名关系桶 / 渲染关系网角色块。
* ``behavioral_dna_block``   — ONTOLOGY：把 worldview/incentives/resources/risk_tolerance 渲染为人格底座块。

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


def _actor_norm_aliases(row: Dict[str, Any]) -> List[str]:
    """actor 行的可选 ``aliases`` 列表 → 标准化别名列表（容错：非 list/非 str 返回 []）。

    KG 经验（Anthropic KG cookbook 核心洞见）：很多同一实体的别名零字符重叠
    （"Edwin Aldrin"=="Buzz Aldrin"、"MSFT"=="Microsoft"、"马斯克"=="Musk"），
    纯字符串相似度无法合并，需要显式别名表来解析。
    """
    if not isinstance(row, dict):
        return []
    raw = row.get("aliases")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for a in raw:
        if isinstance(a, str):
            na = normalize_name(a)
            if na:
                out.append(na)
    return out


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
    短名误配）→ 别名匹配（覆盖零字符重叠的同义名/缩写/外文名）。无匹配返回 None。
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
        if cand:
            if cand == target:
                return row
            # 双向包含；要求重叠名至少 2 个字符，否则噪声太大
            if len(cand) >= 2 and (cand in target or target in cand):
                if len(cand) > best_len:
                    best, best_len = row, len(cand)
        # 别名表（可选）：零字符重叠的别名只能靠显式表解析。精确别名直接命中。
        for al in _actor_norm_aliases(row):
            if al == target:
                return row
            if len(al) >= 2 and (al in target or target in al) and len(al) > best_len:
                best, best_len = row, len(al)
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
    # 角色「内在动机」结构化字段（来自深度研究的 actors-and-incentives 分析）。
    # 让 persona 从「立场标签」升级为「动机画像」；字段缺失时自动跳过，旧档案输出不变。
    for label, key in (("目标/动机", "goals"), ("约束", "constraints"),
                       ("资源/能力", "assets"), ("软肋/红线", "vulnerabilities")):
        val = actor.get(key)
        if isinstance(val, list) and val:
            parts.append(f"- {label}: " + "；".join(str(x) for x in val[:6] if str(x).strip()))
    svr = str(actor.get("stated_vs_revealed", "") or "").strip()
    if svr:
        parts.append(f"- 言行差异: {svr}")
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
            goals = row.get("goals")
            if isinstance(goals, list) and goals and str(goals[0]).strip():
                seg += f" | 核心目标: {str(goals[0]).strip()}"
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
    # ONTOLOGY 扩展类型：经济/治理/媒体边。KG 边名 = 类型本身（恒等），让带价的供需/
    # 出资/持股/制裁/报道等关系不再被压平成无类型的 RELATES_TO，从而保留语义与方向。
    "SUPPLIES": "SUPPLIES",
    "CUSTOMER_OF": "CUSTOMER_OF",
    "FUNDS": "FUNDS",
    "INVESTS_IN": "INVESTS_IN",
    "BACKS": "BACKS",
    "OWNS": "OWNS",
    "SUPPORTS": "SUPPORTS",
    "SANCTIONS": "SANCTIONS",
    "REPORTS_ON": "REPORTS_ON",
    "CONSUMES": "CONSUMES",
    "ENDORSES": "ENDORSES",
    "CRITICIZES": "CRITICIZES",
    "LITIGATES_AGAINST": "LITIGATES_AGAINST",
    # OTHER 是「逃生舱」类型：研究用 relation_label 给出自由文本边名（SUPPLIES/FUNDS/…）。
    # 这里给个兜底边名，graph_builder 会优先用 relation_label，否则落到 RELATES_TO。
    "OTHER": "RELATES_TO",
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
    # ONTOLOGY 扩展类型的双语标签（persona/报告提示块用；保持与旧 8 类同样的简洁口吻）。
    "SUPPLIES": "供应",
    "CUSTOMER_OF": "客户",
    "FUNDS": "出资",
    "INVESTS_IN": "投资",
    "BACKS": "扶持",
    "OWNS": "持有",
    "SUPPORTS": "支持",
    "SANCTIONS": "制裁",
    "REPORTS_ON": "报道",
    "CONSUMES": "消费",
    "ENDORSES": "背书",
    "CRITICIZES": "批评",
    "LITIGATES_AGAINST": "诉讼",
    "OTHER": "关联",
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
    # 端点名集合也并入别名（可选）：让用别名书写的关系端点也能匹配到 actor。
    for r in rows:
        names.update(_actor_norm_aliases(r))
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
        # OTHER 边优先显示研究给出的自由文本标签（如 SUPPLIES / FUNDS），否则用通用「关联」。
        label = REL_LABEL.get(typ, "关联")
        if typ == "OTHER":
            rl = str(r.get("relation_label", "") or "").strip()
            if rl:
                label = rl
        s, t = normalize_name(str(r.get("source", ""))), normalize_name(str(r.get("target", "")))
        strength = str(r.get("strength", "") or "")
        if me == s:
            out.append(f"{label}（{strength}）: {r.get('target')}")
        elif me == t:
            out.append(f"被{label}: {r.get('source')}")
        if len(out) >= max_edges:
            break
    if not out:
        return ""
    return "## 你的社会关系网（调研实证，互动时据此 @ 相关方）\n" + "\n".join("- " + x for x in out)


# ---------------------------------------------------------------------------
# 本体分类 / 行为 DNA / 关系网角色（ONTOLOGY 层 — 让「报道者/概念/资源」不被当作能动 agent，
# 给真正的 actor 注入价值观/动机/资源的人格底座，并把关系投影成命名关系桶）
# ---------------------------------------------------------------------------

# 合法 archetype 枚举（其余值原样保留但按「非 actor」处理）。
ENTITY_ARCHETYPES = (
    "actor", "collective", "institution_rule", "asset_object", "event",
    "signal", "claim_narrative", "constraint_resource", "place_jurisdiction",
    "source", "scenario",
)
# 能动（可作为模拟 agent）的 archetype：只有真实决策主体 / 群体派系才进 agent 池。
_AGENT_ARCHETYPES = frozenset({"actor", "collective"})

# 关系价 → 极性基准值（relation_polarity 在无显式 polarity 时据此推算，并按 strength 缩放）。
_VALENCE_POLARITY = {
    "allied": 0.4,
    "adversarial": -0.4,
    "transactional": 0.15,
    "directional": 0.0,
    "neutral": 0.0,
}
# 关系类型 → 价（无显式 valence 时据此推）。四类语义簇：结盟/对抗/经济/治理-依赖-影响-报道。
_REL_TYPE_VALENCE = {
    # 结盟（正向）
    "ALLY_OF": "allied", "SUPPORTS": "allied", "PARTNERS_WITH": "allied", "ENDORSES": "allied",
    # 对抗（负向）
    "OPPOSES": "adversarial", "COMPETES_WITH": "adversarial", "SANCTIONS": "adversarial",
    "CRITICIZES": "adversarial", "LITIGATES_AGAINST": "adversarial",
    # 经济（交易性）
    "SUPPLIES": "transactional", "CUSTOMER_OF": "transactional", "FUNDS": "transactional",
    "INVESTS_IN": "transactional", "BACKS": "transactional", "OWNS": "transactional",
    "CONSUMES": "transactional",
    # 治理 / 依赖 / 影响 / 报道（有向、非褒贬）
    "REGULATES": "directional", "DEPENDS_ON": "directional", "INFLUENCES": "directional",
    "REPORTS_ON": "directional",
}


def entity_archetype(actor: Optional[Dict[str, Any]]) -> str:
    """读取 actor 的 ``archetype``；缺失/非法返回默认 "actor"（保持旧行为：一切皆能动）。"""
    if not isinstance(actor, dict):
        return "actor"
    raw = str(actor.get("archetype", "") or "").strip().lower()
    return raw if raw in ENTITY_ARCHETYPES else "actor"


def entity_simulation_tier(actor: Optional[Dict[str, Any]]) -> int:
    """读取 actor 的 ``simulation_tier`` ∈ {1,2,3,4}；缺失时按 archetype/type/influence 推断。

    推断规则（与契约一致）：
    * source archetype 或「仅被引用的媒体」→ 3（被动信息源）。
    * 其余非 actor/collective 的 archetype（事件/概念/资源/地点等）→ 4（抽象）。
    * actor/collective：influence=high → 1（核心决策者），否则 → 2（利益相关方）。
    * 完全无信号 → 1（默认能动，保持旧行为）。
    """
    if not isinstance(actor, dict):
        return 1
    raw = actor.get("simulation_tier")
    if isinstance(raw, bool):  # bool 是 int 子类，需先排除
        raw = None
    if isinstance(raw, int) and raw in (1, 2, 3, 4):
        return raw
    if isinstance(raw, str):
        m = re.search(r"[1-4]", raw)
        if m:
            return int(m.group(0))
    arch = entity_archetype(actor)
    if arch == "source":
        return 3
    if arch not in _AGENT_ARCHETYPES:
        return 4
    # actor / collective：按影响力分核心 / 利益相关方
    w = influence_weight(actor)
    if w is not None and w >= INFLUENCE_WEIGHTS["high"]:
        return 1
    return 1 if w is None else 2


def is_agent_eligible(actor: Optional[Dict[str, Any]]) -> bool:
    """能动 agent 准入门：tier ∈ {1,2}（即 archetype 实质为 actor/collective）。

    报道者/媒体出口/抽象概念/资源（tier 3/4）被挡在模拟 agent 池外，避免它们被
    错误地实例化为有行动力的角色。字段全缺时 tier 推断为 1，故旧档案全部放行。
    """
    return entity_simulation_tier(actor) in (1, 2)


def salience_score(actor: Optional[Dict[str, Any]]) -> float:
    """actor 的显著度评分 ∈ [0,1]：优先读 ``salience.score``，否则从 influence 回退。

    回退标度与契约一致：influence high/medium/low → 0.85/0.55/0.3；无信号 → 0.3。
    用于 agent 上限按「显著度」而非「原始度数」排序，让核心方优先入池。
    """
    if not isinstance(actor, dict):
        return 0.3
    sal = actor.get("salience")
    if isinstance(sal, dict):
        score = sal.get("score")
        if isinstance(score, bool):
            score = None
        if isinstance(score, (int, float)):
            return max(0.0, min(1.0, float(score)))
        # 无数值 score 时，退而读 salience.tier 文本
        tier = str(sal.get("tier", "") or "").strip().lower()
        if tier in ("high", "高"):
            return 0.85
        if tier in ("medium", "med", "中"):
            return 0.55
        if tier in ("low", "低"):
            return 0.3
    w = influence_weight(actor)
    if w is None:
        return 0.3
    if w >= INFLUENCE_WEIGHTS["high"]:
        return 0.85
    if w >= INFLUENCE_WEIGHTS["medium"]:
        return 0.55
    return 0.3


def relation_valence(rel: Optional[Dict[str, Any]]) -> str:
    """关系边的价 ∈ {allied, adversarial, transactional, directional, neutral}。

    优先读显式 ``valence``；否则按 type 推（_REL_TYPE_VALENCE）；OTHER/未知 → "neutral"。
    让「盟友 != 对手」「供应商 != 制裁方」在跟随图/情感计算里被正确区分。
    """
    if not isinstance(rel, dict):
        return "neutral"
    raw = str(rel.get("valence", "") or "").strip().lower()
    if raw in _VALENCE_POLARITY:
        return raw
    typ = str(rel.get("type", "") or "").strip().upper()
    return _REL_TYPE_VALENCE.get(typ, "neutral")


def relation_polarity(rel: Optional[Dict[str, Any]]) -> float:
    """关系边的极性 ∈ [-1,1]：优先读显式 ``polarity``，否则从价推（按 strength 缩放）。

    缺省映射：allied→+0.4 / adversarial→-0.4 / transactional→+0.15 / 其余→0.0。
    strength=high/medium/low → ×1.0/0.7/0.45（让强盟友比弱盟友极性更高）。
    """
    if not isinstance(rel, dict):
        return 0.0
    raw = rel.get("polarity")
    if isinstance(raw, bool):
        raw = None
    if isinstance(raw, (int, float)):
        return max(-1.0, min(1.0, float(raw)))
    base = _VALENCE_POLARITY.get(relation_valence(rel), 0.0)
    if base == 0.0:
        return 0.0
    strength = str(rel.get("strength", "") or "").strip().lower()
    scale = 1.0
    if strength in ("high", "高"):
        scale = 1.0
    elif strength in ("medium", "med", "中"):
        scale = 0.7
    elif strength in ("low", "低"):
        scale = 0.45
    return max(-1.0, min(1.0, base * scale))


# relational_roster 的 10 个命名桶 → (关系类型集合, 方向)。方向语义：
#   "out"  = 我是 source 时该 target 入桶；
#   "in"   = 我是 target 时该 source 入桶；
#   "both" = 任一端皆入桶（对称关系：竞争/对立等）。
# 同一类型可同时供应多个桶（如 SUPPLIES：我作为 supplier 的 target 是 customer；
# 我作为 customer 的 source 是 supplier）。
_ROSTER_RULES: Dict[str, List[tuple]] = {
    "allies": [("ALLY_OF", "both"), ("PARTNERS_WITH", "both"), ("SUPPORTS", "in"), ("ENDORSES", "in")],
    "opponents": [("OPPOSES", "both"), ("SANCTIONS", "in"), ("CRITICIZES", "in"),
                  ("LITIGATES_AGAINST", "both")],
    "competitors": [("COMPETES_WITH", "both")],
    # 我的客户：我是 SUPPLIES 的 source，或我是 CUSTOMER_OF 的 target，或 CONSUMES 的 target。
    "customers": [("SUPPLIES", "out"), ("CUSTOMER_OF", "in"), ("CONSUMES", "in")],
    # 我的供应商：我是 SUPPLIES 的 target，或我是 CUSTOMER_OF 的 source，或 CONSUMES 的 source。
    "suppliers": [("SUPPLIES", "in"), ("CUSTOMER_OF", "out"), ("CONSUMES", "out"), ("DEPENDS_ON", "out")],
    # 我的出资方/投资人：我是 FUNDS/INVESTS_IN/BACKS 的 target。
    "backers_investors": [("FUNDS", "in"), ("INVESTS_IN", "in"), ("BACKS", "in")],
    # 我的支持者：SUPPORTS/ENDORSES 的 source。
    "supporters": [("SUPPORTS", "out"), ("ENDORSES", "out")],
    # 监管我的方：REGULATES 的 source（我被监管）。
    "regulators": [("REGULATES", "in")],
    # 依赖我的方：DEPENDS_ON 的 source（依赖方），或我 SUPPLIES 的 target（下游对我有依赖）。
    "dependents": [("DEPENDS_ON", "in"), ("SUPPLIES", "out")],
    "partners": [("PARTNERS_WITH", "both")],
}


def _rel_strength_text(rel: Dict[str, Any]) -> str:
    """关系行的强度文本（兼容 strength；缺省空串）。"""
    return str(rel.get("strength", "") or "").strip()


def relational_roster(actor_name: str, actors: Optional[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """把 relationships[] 投影为以 ``actor_name`` 为中心的 10 个命名关系桶。

    每桶为 [{name, basis, strength}]，按 relationships[] 的出现序去重（同名只取首条）。
    端点用 normalize_name 与 actor 表对齐（兼容别名）。无关系/无匹配返回 10 个空桶。

    桶含义（站在 actor_name 视角）：allies/opponents/competitors/customers/suppliers/
    backers_investors/supporters/regulators/dependents/partners。
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in _ROSTER_RULES}
    if not actor_name:
        return buckets
    rows = extract_relationship_rows(actors)
    if not rows:
        return buckets
    me = normalize_name(actor_name)
    if not me:
        return buckets
    # 每桶已收名集合（去重）。
    seen: Dict[str, set] = {k: set() for k in _ROSTER_RULES}

    def add(bucket: str, other_raw: Any, rel: Dict[str, Any]) -> None:
        name = str(other_raw or "").strip()
        if not name:
            return
        key = normalize_name(name)
        if not key or key == me or key in seen[bucket]:
            return
        seen[bucket].add(key)
        buckets[bucket].append({
            "name": name,
            "basis": str(rel.get("basis", "") or "").strip(),
            "strength": _rel_strength_text(rel),
        })

    for r in rows:
        typ = str(r.get("type", "") or "").strip().upper()
        s_raw, t_raw = r.get("source"), r.get("target")
        s, t = normalize_name(str(s_raw or "")), normalize_name(str(t_raw or ""))
        i_am_source = (s == me)
        i_am_target = (t == me)
        if not (i_am_source or i_am_target):
            continue
        for bucket, rules in _ROSTER_RULES.items():
            for rtype, direction in rules:
                if rtype != typ:
                    continue
                if direction == "out" and i_am_source:
                    add(bucket, t_raw, r)
                elif direction == "in" and i_am_target:
                    add(bucket, s_raw, r)
                elif direction == "both":
                    if i_am_source:
                        add(bucket, t_raw, r)
                    if i_am_target:
                        add(bucket, s_raw, r)
    return buckets


# roster_block 的桶 → 中文小标题（按谈判/竞争语义排序，最实用的在前）。
_ROSTER_BUCKET_LABEL = (
    ("allies", "盟友"),
    ("partners", "伙伴"),
    ("supporters", "支持者"),
    ("backers_investors", "出资方/投资人"),
    ("customers", "客户/下游"),
    ("suppliers", "供应商/上游"),
    ("dependents", "依赖你的方"),
    ("competitors", "竞争对手"),
    ("opponents", "对手/对立方"),
    ("regulators", "监管你的方"),
)


def roster_block(actor_name: str, actors: Optional[Any], max_per_bucket: int = 5) -> str:
    """把 relational_roster 渲染为「## 关系网角色」提示块；无任何关系返回空串。

    比 relationship_briefing 更结构化：按盟友/对手/客户/供应商等分组列出真实命名对手方，
    让 persona 在互动时能精确 @ 到正确阵营。每桶截断到 max_per_bucket。
    """
    roster = relational_roster(actor_name, actors)
    lines: List[str] = []
    for bucket, label in _ROSTER_BUCKET_LABEL:
        items = roster.get(bucket) or []
        if not items:
            continue
        names: List[str] = []
        for it in items[:max_per_bucket]:
            nm = str(it.get("name", "") or "").strip()
            if not nm:
                continue
            strength = str(it.get("strength", "") or "").strip()
            names.append(f"{nm}（{strength}）" if strength else nm)
        if names:
            lines.append(f"- {label}：" + "、".join(names))
    if not lines:
        return ""
    return "## 关系网角色（调研实证：互动时据此 @ 正确阵营）\n" + "\n".join(lines)


def _dna_str_list(value: Any, limit: int) -> List[str]:
    """取出非空字符串列表（容错：非 list 返回 []，逐项 strip 去空）。"""
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for x in value:
        s = str(x or "").strip()
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def behavioral_dna_block(actor: Optional[Dict[str, Any]]) -> str:
    """把 worldview/incentives/resources/risk_tolerance 渲染为「人格底座」提示块。

    缺全部字段时返回空串（旧档案输出不变）。让 persona 从「立场标签」升级为有价值观、
    信念、得失结构（incentives 的 driver/gains_if/loses_if）与风险偏好的稳定人格。
    """
    if not isinstance(actor, dict):
        return ""
    parts: List[str] = []

    wv = actor.get("worldview")
    if isinstance(wv, dict):
        identity = str(wv.get("identity", "") or "").strip()
        frame = str(wv.get("frame", "") or "").strip()
        values = _dna_str_list(wv.get("values"), 6)
        beliefs = _dna_str_list(wv.get("beliefs"), 6)
        if identity:
            parts.append(f"- 身份认同: {identity}")
        if frame:
            parts.append(f"- 看待问题的框架: {frame}")
        if values:
            parts.append("- 核心价值观: " + "；".join(values))
        if beliefs:
            parts.append("- 核心信念: " + "；".join(beliefs))

    incentives = actor.get("incentives")
    if isinstance(incentives, list) and incentives:
        inc_lines: List[str] = []
        for inc in incentives[:6]:
            if isinstance(inc, dict):
                driver = str(inc.get("driver", "") or "").strip()
                gains = str(inc.get("gains_if", "") or "").strip()
                loses = str(inc.get("loses_if", "") or "").strip()
                intensity = str(inc.get("intensity", "") or "").strip()
                if not (driver or gains or loses):
                    continue
                seg = f"  - {driver}" if driver else "  - 动机"
                tail: List[str] = []
                if gains:
                    tail.append(f"得益于「{gains}」")
                if loses:
                    tail.append(f"受损于「{loses}」")
                if tail:
                    seg += "：" + "，".join(tail)
                if intensity:
                    seg += f"（强度 {intensity}）"
                inc_lines.append(seg)
            else:
                s = str(inc or "").strip()  # 容忍纯字符串写法
                if s:
                    inc_lines.append(f"  - {s}")
        if inc_lines:
            parts.append("- 激励结构（得失驱动）:")
            parts.extend(inc_lines)

    # resources 是 assets 的超集/别名：优先 resources，回退 assets。
    resources = _dna_str_list(actor.get("resources"), 8) or _dna_str_list(actor.get("assets"), 8)
    if resources:
        parts.append("- 可调动资源: " + "；".join(resources))

    risk = str(actor.get("risk_tolerance", "") or "").strip()
    if risk:
        parts.append(f"- 风险偏好: {risk}")

    if not parts:
        return ""
    return "## 行为 DNA（持久人格底座，决策时据此保持一致的价值观与得失计算）\n" + "\n".join(parts)


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
        # ---- ONTOLOGY 扩展类型（旧 8 类无新字段时上面已 continue / 命中，下面纯属增量）----
        elif typ in ("SUPPLIES", "CUSTOMER_OF"):
            # 依赖侧关注供给侧：SUPPLIES(source=供应商) → 客户(d) 关注供应商(s)；
            # CUSTOMER_OF(source=客户) → 客户(s) 关注供应商(d)。
            pairs.add((d, s) if typ == "SUPPLIES" else (s, d))
        elif typ in ("FUNDS", "INVESTS_IN", "BACKS"):
            pairs.add((d, s))           # 受资助/被投/被扶持方关注其出资方/投资人/后台
        elif typ == "OWNS":
            pairs.add((d, s))           # 被持有方关注其所有者
        elif typ in ("SUPPORTS", "ENDORSES"):
            pairs.add((d, s))           # 被支持/被背书方关注支持者/背书者
        elif typ in ("SANCTIONS", "LITIGATES_AGAINST", "CRITICIZES"):
            # 制裁/诉讼/批评：双向监视（被针对方紧盯发起方，发起方也持续盯目标）
            pairs.add((s, d))
            pairs.add((d, s))
        # REPORTS_ON / CONSUMES：报道者/消费者不应被建成能动 agent 的关注边（无 follow）。
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


def dossier_coverage(actors: Optional[Any]) -> Dict[str, Any]:
    """NEXTSTEPS P3-4：计算 actors.json 契约中**载荷字段**的覆盖率，让"空壳种子"可被发现。

    actors.py 让每个契约字段都可选并静默降级——这对鲁棒性正确，但一份每个 actor 都缺激励、
    或关系网无边的"空壳" dossier 会与一份丰富的 dossier 无差别地流过，把预测建在空地基上而毫无
    信号。本函数把它量化为可读指标（写进 meta.json / 经研究质量面板暴露 / 可触发 refine 或加宽
    不确定度）。纯函数；actors 为空/畸形时返回零骨架。

    返回：{n_actors, n_relationships, n_tier12, pct_actors_with_incentives,
    pct_tier12_with_worldview, pct_edges_valenced, edges_per_actor, salience_basis_present}。
    """
    zero = {
        "n_actors": 0, "n_relationships": 0, "n_tier12": 0,
        "pct_actors_with_incentives": 0.0, "pct_tier12_with_worldview": 0.0,
        "pct_edges_valenced": 0.0, "edges_per_actor": 0.0, "salience_basis_present": 0.0,
    }
    rows = extract_actor_rows(actors)
    n = len(rows)
    if n == 0:
        return zero

    def _truthy_field(a: Dict[str, Any], key: str) -> bool:
        v = a.get(key)
        return bool(v) if isinstance(v, (list, dict)) else bool(v)

    n_incent = sum(1 for a in rows if _truthy_field(a, "incentives"))
    tier12 = [a for a in rows if entity_simulation_tier(a) in (1, 2)]
    n_wv = sum(1 for a in tier12 if isinstance(a.get("worldview"), dict) and a.get("worldview"))
    n_sal = sum(1 for a in rows if _truthy_field(a, "salience"))

    rels = extract_relationship_rows(actors)
    n_rels = len(rels)
    # 显式 valence 覆盖：只数携带 explicit valence/polarity 字段的边（区别于按类型推断的默认值），
    # 这正是"扁平无 valence 网络"该被检测出的信号。
    n_valenced = sum(
        1 for r in rels
        if isinstance(r, dict) and (r.get("valence") or r.get("polarity") is not None)
    )
    return {
        "n_actors": n,
        "n_relationships": n_rels,
        "n_tier12": len(tier12),
        "pct_actors_with_incentives": round(n_incent / n, 3),
        "pct_tier12_with_worldview": round(n_wv / len(tier12), 3) if tier12 else 0.0,
        "pct_edges_valenced": round(n_valenced / n_rels, 3) if n_rels else 0.0,
        "edges_per_actor": round(n_rels / n, 2),
        "salience_basis_present": round(n_sal / n, 3),
    }


def _row_norm_tokens(row: Dict[str, Any]) -> set:
    """一个 actor 行的标准化"名字 token 集"（主名 + 别名），用于跨轨去重。"""
    toks = set()
    nm = normalize_name(str(row.get("name", "")))
    if nm:
        toks.add(nm)
    for al in _actor_norm_aliases(row):
        if al:
            toks.add(al)
    return toks


def _rows_same_entity(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """两 actor 行是否指向同一实体（保守判定，合并是破坏性的）。

    命中任一：① 标准化名/别名 token 有交集；② 主名双向包含且较短名 ≥4 字符
    （避免 "AI"/"EU" 这类短名误并）。
    """
    ta, tb = _row_norm_tokens(a), _row_norm_tokens(b)
    if ta & tb:
        return True
    pa = normalize_name(str(a.get("name", "")))
    pb = normalize_name(str(b.get("name", "")))
    if pa and pb and min(len(pa), len(pb)) >= 4 and (pa in pb or pb in pa):
        return True
    return False


def reconcile_cast(actors: Optional[Any]) -> tuple:
    """NEXTSTEPS P3-2：抽取后对 actors[] 做跨轨去重，合并指向同一实体的重复行。

    双轨研究刻意产出两份 cast，今天只是被拼接交给一个抽取 LLM 静默仲裁；同一实体的
    重复行（如 "Nvidia" vs "NVIDIA Corp"，可能 role-class 还不同）会**分裂中心度、衍生
    重复 persona、污染 agent-cap 依赖的 salience 排序**。本 pass 用 normalize_name +
    双向包含/别名把重复行聚类，合并为一条规范行（更丰富者胜、并别名、缺字段回填、记冲突），
    并把 relationships 端点改写到规范名。纯函数；返回 (reconciled_actors, audit)。
    actors 非 dict / actor 行<2 → 原样返回 + 空 audit。
    """
    empty_audit = {"merged": [], "n_before": 0, "n_after": 0}
    if not isinstance(actors, dict):
        return actors, empty_audit
    rows = extract_actor_rows(actors)
    n_before = len(rows)
    if n_before < 2:
        return actors, {"merged": [], "n_before": n_before, "n_after": n_before}

    # union-find 聚类
    parent = list(range(n_before))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n_before):
        for j in range(i + 1, n_before):
            if _rows_same_entity(rows[i], rows[j]):
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[rj] = ri

    clusters: Dict[int, List[int]] = {}
    for i in range(n_before):
        clusters.setdefault(_find(i), []).append(i)

    # 无任何重复 → 真正的 no-op：原样返回输入对象（与现状逐字节一致）。
    if all(len(idxs) == 1 for idxs in clusters.values()):
        return actors, {"merged": [], "n_before": n_before, "n_after": n_before}

    _RICH_KEYS = ("role", "stance", "influence", "memory", "incentives", "worldview",
                  "resources", "aliases", "goals", "constraints", "type", "salience")
    _SCALAR_CONFLICT_KEYS = ("role", "stance", "type", "influence")

    def _richness(row: Dict[str, Any]) -> int:
        return sum(1 for k in _RICH_KEYS if row.get(k))

    merged_audit: List[Dict[str, Any]] = []
    new_rows: List[Dict[str, Any]] = []
    rename: Dict[str, str] = {}   # 标准化的 victim 名 → survivor 规范名
    for idxs in clusters.values():
        members = [rows[i] for i in idxs]
        if len(members) == 1:
            new_rows.append(members[0])
            continue
        survivor = max(
            members,
            key=lambda r: (_richness(r), len(r.get("aliases") or []), len(str(r.get("name", "")))),
        )
        merged = dict(survivor)
        aliases = {str(a) for a in (merged.get("aliases") or [])}
        conflicts: List[Dict[str, Any]] = []
        victim_names: List[str] = []
        for m in members:
            if m is survivor:
                continue
            vn = str(m.get("name") or "")
            if vn:
                victim_names.append(vn)
                aliases.add(vn)
            for a in (m.get("aliases") or []):
                aliases.add(str(a))
            for k, v in m.items():
                if k in ("name", "aliases"):
                    continue
                if not merged.get(k) and v:
                    merged[k] = v
                elif (k in _SCALAR_CONFLICT_KEYS and isinstance(v, str)
                      and isinstance(merged.get(k), str) and merged[k] and v and merged[k] != v):
                    conflicts.append({"field": k, "kept": merged[k], "dropped": v, "from": vn})
        aliases.discard(str(merged.get("name") or ""))
        if aliases:
            merged["aliases"] = sorted(aliases)
        new_rows.append(merged)
        canon = str(merged.get("name") or "")
        for vn in victim_names:
            nv = normalize_name(vn)
            if nv:
                rename[nv] = canon
        merged_audit.append({"canonical": canon, "merged": victim_names, "conflicts": conflicts})

    out = dict(actors)
    out["actors"] = new_rows
    rels = actors.get("relationships")
    if isinstance(rels, list) and rename:
        new_rels = []
        for r in rels:
            if not isinstance(r, dict):
                new_rels.append(r)
                continue
            r2 = dict(r)
            for ep in ("source", "target"):
                nv = normalize_name(str(r2.get(ep, "")))
                if nv in rename:
                    r2[ep] = rename[nv]
            new_rels.append(r2)
        out["relationships"] = new_rels

    return out, {"merged": merged_audit, "n_before": n_before, "n_after": len(new_rows)}


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
