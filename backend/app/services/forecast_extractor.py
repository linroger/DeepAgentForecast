"""Structured, machine-readable forecast extraction + citation-grounding audit.

Turns a prose forecast report into a machine-checkable object — explicit
scenarios with calibrated probabilities, key drivers, and resolution criteria —
plus a lightweight audit of how well quantitative claims are cited. This is what
makes the pipeline's output *calibratable and backtestable* rather than a wall of
text. EXECPLAN2: I-3-0 / I-9-1 (structured forecast object), I-3-1 (citation
grounding audit). Optional-degrade: only runs when REPORT_STRUCTURED_FORECAST is on.

Kept dependency-light and side-effect-free (takes an LLM client, returns a dict)
so it is unit-testable offline with a fake client.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _cfg(name: str, default: Any) -> Any:
    """Read a Config flag with a safe default (degrade-safe; never raises).

    All new calibration behavior is gated through this so the DEFAULT/unflagged
    path is byte-identical to the pre-optimization pipeline.
    """
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001 — config import must never break extraction
        return default

# JSON schema the extractor asks the model to fill. Mirrored in the prompt.
_FORECAST_INSTRUCTIONS = """你是预测校准专家。基于下面的预测报告，抽取一个**机器可读**的结构化预测对象。
只输出 JSON，不要解释。字段：
{
  "headline": "一句话核心结论",
  "horizon": "预测时间范围（如 '2030'）",
  "scenarios": [
    {
      "name": "情景名",
      "probability": 0.0-1.0,         // 所有情景概率之和应≈1
      "summary": "该情景的简述",
      "key_drivers": ["关键驱动因素", ...],
      "resolution_criteria": "可证伪的判定标准：到期时如何客观判断此情景是否发生"
    }
  ],
  "key_uncertainties": ["最大的不确定性来源", ...],
  "confidence": "low|medium|high",     // 对整体预测的信心
  "confidence_rationale": "信心评级的理由（数据质量/分歧度/时间跨度）"
}
要求：2-5 个互斥且尽量穷尽的情景；概率为数值；resolution_criteria 必须客观可验证。"""


def _coerce_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_scenarios(scenarios: Any) -> List[Dict[str, Any]]:
    """Validate + normalize scenarios so probabilities are floats summing to ~1.0."""
    if not isinstance(scenarios, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        prob = _coerce_float(s.get("probability"))
        row = {
            "name": str(s.get("name") or "未命名情景"),
            "probability": prob if prob is not None else 0.0,
            "summary": str(s.get("summary") or ""),
            "key_drivers": [str(x) for x in (s.get("key_drivers") or []) if x],
            "resolution_criteria": str(s.get("resolution_criteria") or ""),
        }
        # NEXTSTEPS P2-1（anchor-and-adjust）：保留参考类基率锚点与调整理由（若模型给出），
        # 让最终概率对"外部视角基率 + 案例调整"可审计；缺省则不增字段（degrade-safe）。
        if s.get("base_rate_anchor"):
            row["base_rate_anchor"] = str(s.get("base_rate_anchor"))
        if s.get("adjustment_rationale"):
            row["adjustment_rationale"] = str(s.get("adjustment_rationale"))
        cleaned.append(row)
    total = sum(s["probability"] for s in cleaned)
    if total > 0:
        for s in cleaned:
            s["probability"] = s["probability"] / total
    # R2-CAL-4：对每个保留情景设下限再重新归一，避免「已实现但被预测到≈0%」的灾难性
    # log-loss/Brier。默认 0.0（无下限）→ 与旧路径逐字节一致；config 置 0.03 后生效。
    floor = _coerce_float(_cfg("FORECAST_PROB_FLOOR", 0.0)) or 0.0
    if cleaned and floor > 0:
        for s in cleaned:
            if s["probability"] < floor:
                s["probability"] = floor
        t2 = sum(s["probability"] for s in cleaned)
        if t2 > 0:
            for s in cleaned:
                s["probability"] = s["probability"] / t2
    for s in cleaned:
        s["probability"] = round(s["probability"], 4)
    return cleaned


# ---------------------------------------------------------- resolution sharpness
# A "sharp" resolution criterion must pin down WHAT (a metric/threshold), HOW MUCH
# (a number) and WHEN (a date/trigger) so the forecast is later trackable & scorable
# — the difference between "利率可能上升" and "若 X 于 Z 日前超过 Y% 则确认".
_RC_DATE_RE = re.compile(
    r"20\d{2}|Q[1-4]|H[12]|[年月日季]|\b\d{1,2}/\d{1,2}\b|前|底|内|by\s|until|before|deadline",
    re.I)
_RC_METRIC_RE = re.compile(
    r"[<>＜＞≥≤%]|份额|份額|占比|增速|增长|价格|股价|指数|数量|规模|阈值|超过|低于|达到|不低于|不超过|至少|以上|以下",
    re.I)


def validate_resolution_criteria(text: Any) -> Dict[str, bool]:
    """Heuristic check that a resolution criterion is sharp (number + date + metric).

    Pure / offline. ``sharp`` is True only when all three signals are present so a
    later auditor can objectively decide whether the scenario occurred. Used to
    populate ``quality.vague_criteria`` (R2-CAL-7). Degrade-safe: never raises.
    """
    t = str(text or "")
    has_number = bool(re.search(r"\d", t))
    has_date = bool(_RC_DATE_RE.search(t))
    has_metric = bool(_RC_METRIC_RE.search(t))
    return {
        "sharp": bool(has_number and has_date and has_metric),
        "has_number": has_number,
        "has_date": has_date,
        "has_metric": has_metric,
    }


def _quality_from_scenarios(scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute degrade-safe quality flags from a scenario list (gated by flags).

    Returns only the keys whose gating flag is on; an empty dict otherwise so the
    default path adds no ``quality`` block at all (schema unchanged when unflagged).
    """
    q: Dict[str, Any] = {}
    if _cfg("REPORT_REQUIRE_SHARP_CRITERIA", False):
        # R2-CAL-7：标记判定标准过于模糊（缺数字/日期/指标之一）的情景。
        vague = [s.get("name") for s in scenarios
                 if not validate_resolution_criteria(s.get("resolution_criteria")).get("sharp")]
        q["vague_criteria"] = [str(n) for n in vague if n]
    if _cfg("REPORT_REQUIRE_ANCHOR", False):
        # R2-CAL-12：标记缺少 base_rate_anchor / adjustment_rationale 的情景（anchor-and-adjust 不可审计），
        # 以及锚点→最终概率出现"无理由大跳"（>0.35）的情景。
        missing = []
        unjustified = []
        for s in scenarios:
            anchor = str(s.get("base_rate_anchor") or "").strip()
            rationale = str(s.get("adjustment_rationale") or "").strip()
            if not (anchor and rationale):
                missing.append(s.get("name"))
            a = _anchor_to_prob(anchor)
            p = _coerce_float(s.get("probability"))
            if a is not None and p is not None and not rationale and abs(p - a) > 0.35:
                unjustified.append(s.get("name"))
        q["missing_anchor"] = [str(n) for n in missing if n]
        if unjustified:
            q["unjustified_anchor_jump"] = [str(n) for n in unjustified if n]
    return q


def _anchor_to_prob(anchor: str) -> Optional[float]:
    """Pull a leading base-rate percentage/fraction out of a free-text anchor, if any."""
    if not anchor:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", anchor)
    if m:
        return max(0.0, min(1.0, float(m.group(1)) / 100.0))
    m = re.search(r"0?\.\d+", anchor)
    if m:
        try:
            return max(0.0, min(1.0, float(m.group(0))))
        except ValueError:
            return None
    return None


def slice_head_tail(text: str, budget: int, head_ratio: float = 0.6) -> str:
    """RQ-2：在同一字符预算内取「前 head_ratio + 后 (1-head_ratio)」两段拼接。

    成稿后抽取此前只取正文开头 [:budget]，但预测报告的收敛判断（"综上/因此/展望/
    结论"）几乎总在文末——head-only 截断恰好把最关键的结论切掉。本函数取前段（导言/
    框架）与尾段（结论/情景概率）各一半预算并以省略标记拼接，让抽取同时看到问题设定与
    最终判断。Pure / offline：budget 覆盖全文或非正 → 原样返回（degrade-safe）。
    """
    t = text or ""
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        return t
    if budget <= 0 or len(t) <= budget:
        return t
    try:
        hr = float(head_ratio)
    except (TypeError, ValueError):
        hr = 0.6
    hr = min(0.95, max(0.05, hr))
    head_n = int(budget * hr)
    tail_n = budget - head_n
    if head_n <= 0 or tail_n <= 0:
        return t[:budget]
    return t[:head_n] + "\n…(中段略)…\n" + t[-tail_n:]


def extract_structured_forecast(report_markdown: str, llm,
                                situation_brief: Optional[str] = None) -> Dict[str, Any]:
    """Run one LLM pass to produce a validated structured forecast object.

    ``llm`` must expose ``chat_json(messages, temperature, max_tokens)``. Returns a
    dict with normalized scenarios; raises nothing on a malformed model reply
    beyond what chat_json raises (caller wraps in try/except and degrades).

    RQ-2：成稿后切片改为 head+tail（结论在文末，不能只取开头）；抽取 max_tokens 2048→4096
    （5 情景 × anchor/rationale/criteria 常被 2048 截断成断裂 JSON）。默认值即新行为。
    """
    content = report_markdown or ""
    budget = int(_cfg("FORECAST_EXTRACT_BUDGET", 40000))
    head_ratio = _coerce_float(_cfg("FORECAST_EXTRACT_HEAD_RATIO", 0.6))
    if head_ratio is None:
        head_ratio = 0.6
    content = slice_head_tail(content, budget, head_ratio)
    user = _FORECAST_INSTRUCTIONS
    if situation_brief:
        user += f"\n\n[态势简报]\n{situation_brief[:2000]}"
    user += f"\n\n[预测报告]\n{content}"
    raw = llm.chat_json(
        messages=[{"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=int(_cfg("FORECAST_EXTRACT_MAX_TOKENS", 4096)),
    )
    if not isinstance(raw, dict):
        raw = {}
    return _assemble_forecast(raw)


def _assemble_forecast(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw model JSON into the canonical structured-forecast dict.

    Shared by ``extract_structured_forecast`` (post-hoc, from prose) and
    ``derive_forecast_spine`` (NEXTSTEPS P0-1, from signals) so both emit an
    identical shape regardless of where the probabilities came from.
    """
    scenarios = _normalize_scenarios(raw.get("scenarios"))
    confidence = str(raw.get("confidence") or "medium").lower()
    if confidence not in ("low", "medium", "high"):
        confidence = "medium"
    out = {
        "headline": str(raw.get("headline") or ""),
        "horizon": str(raw.get("horizon") or ""),
        "scenarios": scenarios,
        "key_uncertainties": [str(x) for x in (raw.get("key_uncertainties") or []) if x],
        "confidence": confidence,
        "confidence_rationale": str(raw.get("confidence_rationale") or ""),
        "schema_version": 1,
    }
    # R2-CAL-7 / R2-CAL-12：仅当相应旗标开启时附加 quality 诊断块（默认不加 → schema 不变）。
    quality = _quality_from_scenarios(scenarios)
    if quality:
        out["quality"] = quality
    return out


# ----------------------------------------------------- binary forecasts (QUALITY-OPT A1)
# Many briefs (e.g. Bridgewater) demand the headline deliverable as a SET OF INDEPENDENT
# BINARY yes/no forecasts — one sentence, a probability, and an OBJECTIVE resolution test —
# NOT a set of mutually-exclusive scenarios that sum to 1. The research stage already tends
# to produce these (e.g. an F1..Fn table); the scenario-only finalizer discarded them. This
# pathway extracts/derives >=min_count independent binaries and keeps them ALONGSIDE the
# scenario spine, so both the calibratable scenario view and the brief's contract survive.
_BINARY_FORECAST_INSTRUCTIONS = (
    "You are a forecasting-calibration expert assembling the HEADLINE deliverable: a set of "
    "INDEPENDENT BINARY (yes/no) forecasts. From the research dossier below, FIRST extract "
    "every binary forecast the analyst already stated (preserve their probabilities and "
    "resolution criteria verbatim where given); THEN, if fewer than {min_count}, derive "
    "additional ones from the dossier's drivers, indicators, and quantitative facts. Never "
    "invent facts the dossier does not support.\n\n"
    "Output JSON ONLY: {{\"binary_forecasts\": [ {{...}}, ... ]}}. Each object:\n"
    "{{\n"
    '  "id": "F1",\n'
    '  "statement": "ONE declarative sentence that resolves strictly yes/no, with the number and date INSIDE it (model: \\"The US effective tariff rate on imports averages over 10% from 2026-2028\\").",\n'
    '  "probability": 0.02-0.98,        // INDEPENDENT per forecast — do NOT make these sum to 1\n'
    '  "resolution_criteria": "Objective settle test: a named METRIC + a NUMERIC threshold + a DATE/window + the SOURCE that resolves it.",\n'
    '  "resolution_source": "the dataset/agency/publication that will settle it",\n'
    '  "theme": "{theme_enum}",\n'
    '  "horizon_year": 2027,            // resolution year, within 1-5 years of now\n'
    '  "base_rate_anchor": "reference-class base rate / outside view",\n'
    '  "adjustment_rationale": "why this case differs from the base rate (anchor-and-adjust)",\n'
    '  "source": "provenance of the probability: name the simulation signal that moved it (e.g. \\"world-state outcome shares\\", \\"coalition map\\") or \\"research-prior\\" when only research evidence informs it"\n'
    "}}\n\n"
    "RULES: produce AT LEAST {min_count} DISTINCT forecasts. Every statement is ONE sentence, "
    "falsifiable, and {tie_rule}. Probabilities "
    "express genuine CONVICTION — do not cluster in 0.40-0.60; commit where the evidence "
    "warrants. Each resolution_criteria MUST contain a metric, a number, and a date. Write all "
    "text in {language}."
)

# RPT-6：主题不再硬编码为 Bridgewater 三元组；未显式给 themes 时用主题无关措辞 + 自由主题串。
_BINARY_DEFAULT_THEME_ENUM = "one short lowercase tag naming the driving force this forecast belongs to"
_BINARY_DEFAULT_TIE_RULE = "tied to the report's central question and its key driving forces"

# RPT-4（FORECAST_BINARY_CONTRARIAN，默认开）：单向框架会让全部概率挤在 0.5 以上、
# stdev 永远过不了 0.12 门；要求近半数陈述以「证据支持概率 < 0.5」的反共识方向直接表述。
_BINARY_CONTRARIAN_RULE = (
    "\nCONTRARIAN FRAMING: frame roughly 40-50% of the statements so that the "
    "evidence-supported probability is BELOW 0.5 — assert the counter-consensus outcome "
    "directly (e.g. \"X exceeds Y by Z date\" priced at 0.25). Do NOT achieve this by "
    "negating another statement in the set."
)

# RPT-4：首轮全部 >0.5 / spread 过低时的一次有界重述补足（仅低概率陈述）。
_BINARY_LOW_P_RULE = (
    "\nIMPORTANT: EVERY new statement must carry an evidence-supported probability in the "
    "0.05-0.35 range — assert specific, falsifiable counter-consensus outcomes directly "
    "(not negations of earlier statements)."
)

# 预测市场校准锚点（Polymarket 公开 Gamma API）：与所列市场重叠的预测须引用市场
# 隐含概率，偏离 >10 个百分点须显式解释分歧；市场是校准锚点，不是真值。命中时模型给出
# market_anchor 字段，_normalize_binaries 用我们自己的市场数据回填/校验隐含概率并计算
# divergence（不盲信模型转录的数字）。
_BINARY_MARKET_RULE = (
    "\nMARKET CALIBRATION: real prediction-market implied probabilities "
    "(Polymarket) are listed below. Where a forecast overlaps a listed "
    "market, CITE that market's implied probability in adjustment_rationale, and when your "
    "probability diverges from it by MORE than 10 percentage points, EXPLAIN the divergence "
    "explicitly (what the market is missing or mispricing). Markets are calibration anchors, "
    "NOT ground truth — do not blindly copy them. For each such overlapping forecast add an "
    "extra field \"market_anchor\": {\"market_id\": \"<id from the table>\", "
    "\"implied_yes_prob\": 0.0-1.0}; OMIT market_anchor entirely when no listed market applies."
)


def _binary_key(stmt: str) -> str:
    return re.sub(r"\W+", " ", str(stmt or "").lower()).strip()


def _normalize_binaries(items: Any, *, start_index: int = 1,
                        allowed_themes: Optional[List[str]] = None,
                        market_lookup: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """Clamp/round each probability INDEPENDENTLY (no sum-normalization), dedup by
    statement, attach an objective-criteria quality flag. Drops rows missing a
    statement or a numeric probability.

    RPT-6: ``allowed_themes`` 给出时未知主题被钳到末位（历史三元组下逐字节等价于旧
    「非法→intersection」行为）；未给出时保留模型原话（小写），主题不再被硬编码扭曲。
    XRUN-1(a): 每条预测附带 ``source`` 溯源字段（缺省 'research-prior'），让每个概率
    可追责到具体模拟信号或研究先验。
    预测市场锚点：模型给出 market_anchor 时校验并保留 {market_id, implied_yes_prob,
    divergence}；``market_lookup``（market_id→隐含概率，来自我们抓取的快照）命中时
    以快照价回填 implied_yes_prob（不盲信模型转录），divergence 一律由本函数确定性
    计算（本预测概率 − 市场隐含概率）。锚点非法/缺失时不加字段（degrade-safe）。"""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        stmt = str(it.get("statement") or "").strip()
        p = _coerce_float(it.get("probability"))
        if not stmt or p is None:
            continue
        key = _binary_key(stmt)
        if not key or key in seen:
            continue
        seen.add(key)
        p = max(0.02, min(0.98, p))
        rc = str(it.get("resolution_criteria") or "")
        hy_raw = it.get("horizon_year")
        try:
            hy = int(float(hy_raw)) if hy_raw not in (None, "") else None
        except (TypeError, ValueError):
            hy = None
        theme = str(it.get("theme") or "").strip().lower()
        if allowed_themes:
            if theme not in allowed_themes:
                theme = allowed_themes[-1]
        elif not theme:
            theme = "general"
        row = {
            "id": str(it.get("id") or f"F{start_index + len(out)}"),
            "statement": stmt,
            "probability": round(p, 2),
            "resolution_criteria": rc,
            "resolution_source": str(it.get("resolution_source") or ""),
            "theme": theme,
            "horizon_year": hy,
            "base_rate_anchor": str(it.get("base_rate_anchor") or ""),
            "adjustment_rationale": str(it.get("adjustment_rationale") or ""),
            "source": str(it.get("source") or "").strip() or "research-prior",
            "criteria_sharp": bool(validate_resolution_criteria(rc).get("sharp")),
        }
        anchor = it.get("market_anchor")
        if isinstance(anchor, dict):
            mid = str(anchor.get("market_id") or "").strip()
            ip = _coerce_float(anchor.get("implied_yes_prob"))
            if market_lookup and mid in market_lookup:
                ip = _coerce_float(market_lookup.get(mid))  # 以我们的快照价为准
            if mid and ip is not None and 0.0 <= ip <= 1.0:
                row["market_anchor"] = {
                    "market_id": mid,
                    "implied_yes_prob": round(ip, 4),
                    "divergence": round(row["probability"] - ip, 4),
                }
        out.append(row)
    return out


def _binary_quality(binaries: List[Dict[str, Any]], *, min_count: int,
                    themes_expected: Optional[List[str]] = None) -> Dict[str, Any]:
    """Conviction + objectivity scorecard for a binary set (QUALITY-OPT A3/A4).

    Flags the two failure modes the brief calls out: hedging (everything near 0.5)
    and vague criteria. ``passed`` is the publish gate.
    RPT-6: 主题统计改为「配置的期望主题（补零）+ 实际观察到的主题」，不再硬编码三元组。
    """
    n = len(binaries)
    probs = [b["probability"] for b in binaries]
    mean = sum(probs) / n if n else 0.0
    std = math.sqrt(sum((p - mean) ** 2 for p in probs) / n) if n else 0.0
    midband = sum(1 for p in probs if 0.40 <= p <= 0.60)
    conviction = sum(1 for p in probs if p >= 0.70 or p <= 0.30)
    sharp = sum(1 for b in binaries if b.get("criteria_sharp"))
    themes: Dict[str, int] = {str(t): 0 for t in (themes_expected or [])}
    for b in binaries:
        t = str(b.get("theme") or "")
        if t:
            themes[t] = themes.get(t, 0) + 1
    # RQ-2：主题基数（实际出现的不同主题数）。二元集合全部塌成同一主题（如 _normalize_binaries
    # 把未知主题钳到残余主题、或模型对所有条目复读同一 theme）= 主题多样性丢失，交付物看似
    # 覆盖多个驱动力实则单一。基数<=1 时记诊断标记（不阻断发布门，避免回归既有合法单主题跑）。
    theme_cardinality = sum(1 for _t, _c in themes.items() if _c > 0)
    midband_share = (midband / n) if n else 1.0
    passed = (
        n >= min_count
        and std >= 0.12               # genuine spread, not all-hedged
        and midband_share <= 0.40     # not a wall of coin-flips
        and conviction >= 3           # at least a few committed calls
        and sharp >= int(0.8 * n)     # mostly objective criteria
    )
    issues = []
    if n < min_count:
        issues.append(f"only {n} binaries (< {min_count})")
    if std < 0.12:
        issues.append(f"probability spread too low (stdev {std:.2f}) — hedging")
    if midband_share > 0.40:
        issues.append(f"{midband}/{n} forecasts in 0.40-0.60 — under-committed")
    if conviction < 3:
        issues.append("fewer than 3 high-conviction calls (p>=0.70 or <=0.30)")
    if sharp < int(0.8 * n):
        issues.append(f"only {sharp}/{n} have objective metric+number+date criteria")
    if n > 1 and theme_cardinality <= 1:
        issues.append("all forecasts share a single theme — no thematic spread")
    return {
        "count": n, "prob_stdev": round(std, 3), "midband_share": round(midband_share, 3),
        "conviction_count": conviction, "sharp_criteria_count": sharp, "themes": themes,
        "theme_cardinality": theme_cardinality,
        "passed": bool(passed), "issues": issues,
    }


# --------------------------------------------- PM-2: deterministic market anchoring
# 此前市场锚点走「模型自愿在 market_anchor 里给 market_id」的 opt-in 路径，取证 0/13 与
# 0/11 条二元被锚定——模型几乎从不主动转录 id。PM-2 改为**确定性**：先跑一次批处理 LLM
# 匹配（预测陈述表 × 相关性门控后的市场表[含 endDate]），每条预测得 {market_id|null,
# resolution_equivalence: exact|near|loose, confidence}；再由本模块确定性回填 rich
# market_anchor（question/implied/price_at_research/url/endDate/divergence，隐含概率一律
# 以我们的快照价为准、divergence 本地计算）。匹配调用失败/无市场 → 不加锚点（今日行为）。
_MARKET_MATCH_INSTRUCTIONS = (
    "You are matching binary forecasts to real prediction markets for CALIBRATION. Below is a "
    "numbered list of binary (yes/no) FORECASTS and a list of live prediction MARKETS (each with "
    "an implied YES probability and a market end date). For EACH forecast decide whether ANY "
    "listed market resolves to the SAME real-world event on a compatible timeframe.\n"
    "Return JSON ONLY: {\"matches\": [ {\"forecast_id\": \"F1\", "
    "\"market_id\": \"<id copied verbatim from the MARKETS list, or null>\", "
    "\"resolution_equivalence\": \"exact|near|loose\", \"confidence\": 0.0-1.0} , ... ] }\n"
    "RULES: market_id MUST be copied verbatim from the MARKETS list or be null when nothing "
    "matches — never invent an id. resolution_equivalence = exact (same metric, threshold AND "
    "window), near (same event, slightly different threshold/date), loose (related theme only). "
    "A forecast matches at most ONE market; a market may be cited by several forecasts."
)

# 10pp 分歧有界重述：锚定后 |model_p − market_p| > 0.10 且理由未提及市场的预测，做一次
# 批处理重述——模型须**要么**把概率移向市场、**要么**保留分歧，但两种情形都必须在
# adjustment_rationale 里显式引用市场隐含概率并解释（绝不静默移动概率）。
_MARKET_DIVERGENCE_INSTRUCTIONS = (
    "You are reconciling forecasts against live prediction-market prices. Each item below is a "
    "binary forecast whose probability diverges from a matched market's implied probability by "
    "MORE than 10 percentage points, and whose rationale does NOT yet address that market. For "
    "EACH item decide deliberately: EITHER move your probability toward the market, OR keep your "
    "divergence — but in BOTH cases you MUST rewrite adjustment_rationale to explicitly cite the "
    "market's implied probability and explain the divergence (what the market is missing / "
    "mispricing, or why you now defer to it). Markets are calibration anchors, not ground truth.\n"
    "Return JSON ONLY: {\"revisions\": [ {\"id\": \"F1\", \"probability\": 0.02-0.98, "
    "\"adjustment_rationale\": \"...must mention the market and its implied probability...\"} , ... ] }"
)

_MARKET_EQUIVALENCE_RANK = {"exact": 3, "near": 2, "loose": 1}
# 理由「提及市场」的判据：命中关键词（market/Polymarket/市场/预测市场/implied）即算。
_MARKET_MENTION_RE = re.compile(r"market|polymarket|市场|預測|预测市场|implied", re.I)


def _market_end_date(market: Dict[str, Any]) -> str:
    """规整化市场快照用 end_date（见 prediction_markets），锚点对外用 endDate（camelCase）。"""
    return str((market or {}).get("end_date") or (market or {}).get("endDate") or "").strip()


def _rationale_cites_market(text: Any, anchor: Optional[Dict[str, Any]] = None) -> bool:
    """理由是否引用了市场——关键词命中，或文本里出现了市场隐含概率的百分数。Pure。"""
    t = str(text or "")
    if not t.strip():
        return False
    if _MARKET_MENTION_RE.search(t):
        return True
    if isinstance(anchor, dict):
        ip = _coerce_float(anchor.get("implied_yes_prob"))
        if ip is not None and re.search(rf"\b{int(round(ip * 100))}\s*%", t):
            return True
    return False


def _build_market_anchor(prob: Optional[float], market: Dict[str, Any], *,
                         equivalence: Optional[str] = None,
                         match_confidence: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """确定性组装 rich market_anchor：隐含概率取我们的快照价，divergence 本地计算。

    price_at_research 记研究时点的快照价（与 implied_yes_prob 同源，供日后与实时价对比）。
    市场缺 id / 隐含概率非法 → None（不加锚点，degrade-safe）。"""
    mid = str((market or {}).get("market_id") or "").strip()
    ip = _coerce_float((market or {}).get("implied_yes_prob"))
    p = _coerce_float(prob)
    if not mid or ip is None or not (0.0 <= ip <= 1.0):
        return None
    anchor: Dict[str, Any] = {
        "market_id": mid,
        "question": str(market.get("question") or ""),
        "implied_yes_prob": round(ip, 4),
        "price_at_research": round(ip, 4),
        "divergence": round((p if p is not None else 0.0) - ip, 4),
    }
    url = str(market.get("url") or "").strip()
    if url:
        anchor["url"] = url
    ed = _market_end_date(market)
    if ed:
        anchor["endDate"] = ed
    eq = str(equivalence or "").strip().lower()
    if eq:
        anchor["resolution_equivalence"] = eq
    mc = _coerce_float(match_confidence)
    if mc is not None:
        anchor["match_confidence"] = round(max(0.0, min(1.0, mc)), 3)
    return anchor


def anchor_binaries_to_markets(binaries: List[Dict[str, Any]], markets: Optional[List[Dict[str, Any]]],
                               llm, *, language: str = "English", max_markets: int = 24) -> int:
    """PM-2：一次批处理 LLM 匹配 + 确定性回填 market_anchor（就地改写 binaries）。返回锚定条数。

    只接受 resolution_equivalence 严格度 ≥ FORECAST_MARKET_ANCHOR_MIN_EQUIVALENCE（默认 near，
    即 exact/near 采纳、loose 丢弃）的匹配。旗标 FORECAST_MARKET_ANCHORING 关闭 / 无市场 /
    无二元 / 匹配调用异常或非法 JSON → 不加锚点（今日行为，degrade-safe）。"""
    if not _cfg("FORECAST_MARKET_ANCHORING", True):
        return 0
    bins = [b for b in (binaries or []) if isinstance(b, dict) and str(b.get("statement") or "").strip()]
    mkts = []
    for m in (markets or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("market_id") or "").strip()
        ip = _coerce_float(m.get("implied_yes_prob"))
        if mid and ip is not None and 0.0 <= ip <= 1.0:
            mkts.append(m)
    if not bins or not mkts:
        return 0
    mkts = mkts[:max_markets]
    market_by_id = {str(m.get("market_id")).strip(): m for m in mkts}
    min_eq = str(_cfg("FORECAST_MARKET_ANCHOR_MIN_EQUIVALENCE", "near")).strip().lower()
    min_rank = _MARKET_EQUIVALENCE_RANK.get(min_eq, 2)
    flines = [f"[{b.get('id')}] {str(b.get('statement') or '')[:220]}" for b in bins]
    mlines = []
    for m in mkts:
        ip = _coerce_float(m.get("implied_yes_prob"))
        pct = f"{ip * 100:.0f}%" if ip is not None else "—"
        ed = _market_end_date(m)
        mlines.append(f"[{str(m.get('market_id')).strip()}] {str(m.get('question') or '')[:200]} "
                      f"(implied YES {pct}{', ends ' + ed if ed else ''})")
    user = (_MARKET_MATCH_INSTRUCTIONS + f"\n\nWrite any prose in {language}."
            + "\n\n[FORECASTS]\n" + "\n".join(flines)
            + "\n\n[MARKETS]\n" + "\n".join(mlines))
    try:
        raw = llm.chat_json(messages=[{"role": "user", "content": user}],
                            temperature=0.1, max_tokens=1500)
    except Exception as _me:  # noqa: BLE001 — 匹配失败 → 不加锚点（degrade-safe）
        logger.warning(f"预测市场匹配调用失败（忽略，不加锚点）: {_me}")
        return 0
    matches = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(matches, list):
        return 0
    bin_by_id = {str(b.get("id")): b for b in bins}
    anchored = 0
    for mt in matches:
        if not isinstance(mt, dict):
            continue
        fid = str(mt.get("forecast_id") or "").strip()
        mid = str(mt.get("market_id") or "").strip()
        if not mid or mid.lower() == "null":
            continue
        b = bin_by_id.get(fid)
        m = market_by_id.get(mid)
        if b is None or m is None:
            continue
        eq = str(mt.get("resolution_equivalence") or "").strip().lower()
        if _MARKET_EQUIVALENCE_RANK.get(eq, 0) < min_rank:
            continue  # 严格度不足（如 loose）→ 不锚定
        anchor = _build_market_anchor(b.get("probability"), m, equivalence=eq,
                                      match_confidence=mt.get("confidence"))
        if anchor:
            b["market_anchor"] = anchor  # 确定性回填（覆盖模型自愿转录的最小锚点）
            anchored += 1
    return anchored


def enforce_market_divergence(binaries: List[Dict[str, Any]], llm, *,
                              language: str = "English") -> int:
    """PM-2 的 10pp 规则：锚定后 |divergence|>0.10 且理由未提及市场的预测做一次有界重述。

    重述须在理由中引用市场；否则不接受（绝不静默移动概率）。就地改写 binaries，重算
    market_anchor.divergence。旗标 FORECAST_MARKET_DIVERGENCE_REVISION 关闭 / 无候选 /
    调用异常 → 原样返回。返回被接受的重述条数。"""
    if not _cfg("FORECAST_MARKET_DIVERGENCE_REVISION", True):
        return 0
    candidates = []
    for b in (binaries or []):
        if not isinstance(b, dict):
            continue
        anchor = b.get("market_anchor")
        if not isinstance(anchor, dict):
            continue
        dv = _coerce_float(anchor.get("divergence"))
        if dv is None or abs(dv) <= 0.10:
            continue
        if _rationale_cites_market(b.get("adjustment_rationale"), anchor):
            continue  # 已解释分歧 → 无需重述
        candidates.append(b)
    if not candidates:
        return 0
    items = []
    for b in candidates:
        anchor = b["market_anchor"]
        ip = _coerce_float(anchor.get("implied_yes_prob"))
        items.append(
            f"[{b.get('id')}] statement: {str(b.get('statement') or '')[:220]}\n"
            f"    your probability: {b.get('probability')}; market implied YES: {ip}; "
            f"market question: {str(anchor.get('question') or '')[:160]}\n"
            f"    current rationale: {str(b.get('adjustment_rationale') or '')[:200]}")
    user = (_MARKET_DIVERGENCE_INSTRUCTIONS + f"\n\nWrite all text in {language}."
            + "\n\n[Divergent forecasts]\n" + "\n".join(items))
    try:
        raw = llm.chat_json(messages=[{"role": "user", "content": user}],
                            temperature=0.2, max_tokens=2048)
    except Exception as _re:  # noqa: BLE001 — 重述失败 → 保留原分歧（degrade-safe）
        logger.warning(f"预测市场分歧重述调用失败（忽略，保留原概率/理由）: {_re}")
        return 0
    revs = raw.get("revisions") if isinstance(raw, dict) else None
    if not isinstance(revs, list):
        return 0
    by_id = {str(b.get("id")): b for b in candidates}
    revised = 0
    for r in revs:
        if not isinstance(r, dict):
            continue
        b = by_id.get(str(r.get("id") or "").strip())
        if b is None:
            continue
        anchor = b.get("market_anchor")
        if not isinstance(anchor, dict):
            continue
        new_rat = str(r.get("adjustment_rationale") or "").strip()
        if not new_rat or not _rationale_cites_market(new_rat, anchor):
            continue  # 重述未引用市场 → 拒绝（绝不静默移动概率）
        b["adjustment_rationale"] = new_rat
        new_p = _coerce_float(r.get("probability"))
        if new_p is not None:
            new_p = round(max(0.02, min(0.98, new_p)), 2)
            b["probability"] = new_p
            ip = _coerce_float(anchor.get("implied_yes_prob"))
            if ip is not None:
                anchor["divergence"] = round(new_p - ip, 4)
        revised += 1
    return revised


def build_market_comparison(binaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """PM-2：从已锚定的二元预测汇出确定性的 market_comparison 负载（供落 market_comparison.json）。

    Pure / 无副作用：扫描 binaries 的 market_anchor，逐条给出 预测概率 vs 市场隐含概率、
    分歧、是否超 10pp、是否已在理由中引用市场。无锚定预测 → comparisons 为空列表。"""
    comps: List[Dict[str, Any]] = []
    for b in (binaries or []):
        if not isinstance(b, dict):
            continue
        anchor = b.get("market_anchor")
        if not isinstance(anchor, dict):
            continue
        p = _coerce_float(b.get("probability"))
        dv = _coerce_float(anchor.get("divergence"))
        comps.append({
            "forecast_id": b.get("id"),
            "statement": b.get("statement"),
            "model_probability": p,
            "market_id": anchor.get("market_id"),
            "market_question": anchor.get("question"),
            "market_implied_yes_prob": _coerce_float(anchor.get("implied_yes_prob")),
            "price_at_research": _coerce_float(anchor.get("price_at_research")),
            "divergence": dv,
            "abs_divergence": round(abs(dv), 4) if dv is not None else None,
            "exceeds_10pp": (abs(dv) > 0.10) if dv is not None else False,
            "resolution_equivalence": anchor.get("resolution_equivalence"),
            "match_confidence": anchor.get("match_confidence"),
            "url": anchor.get("url"),
            "endDate": anchor.get("endDate"),
            "rationale_cites_market": _rationale_cites_market(b.get("adjustment_rationale"), anchor),
        })
    return {"anchored_count": len(comps), "comparisons": comps}


# --------------------------------------------------- ITEM 12: multi-model ensemble
def _build_ensemble_client(provider: str) -> Any:
    """ITEM 12：按（副模型）提供方名构造一个 LLMClient，复用 llm_client 的提供方选择机制。

    与 LLMClient/Config 的现有约定完全一致：
      - CLI 订阅提供方（claude-cli / codex-cli，PROVIDER_META 里 openai_compat=False）无需 Key，
        直接 ``LLMClient(provider=p)``；
      - OpenAI 兼容提供方从 PROVIDER_META 取 default_base/default_model，Key 依次尝试
        ``<PROVIDER>_API_KEY`` → 该提供方的 key_env（如 DEEPSEEK_API_KEY）→ 当且仅当与主提供方
        同名时的 Config.LLM_API_KEY；缺 Key 时 LLMClient 构造抛 ValueError。
    未知提供方（不在 PROVIDER_META）→ LLMClient 构造抛 ValueError。两类异常都由
    ``_run_ensemble_draws`` 捕获 → 跳过该模型并记 flag（绝不阻断主抽取）。"""
    import os as _os
    from ..config import Config
    from ..utils.llm_client import LLMClient
    p = str(provider or "").strip().lower()
    meta = Config.PROVIDER_META.get(p) or {}
    if not meta.get("openai_compat"):
        # CLI 订阅提供方 / 未知名：交给 LLMClient 构造决定（合法 CLI 通过，未知名抛 ValueError）。
        return LLMClient(provider=p)
    key = (_os.environ.get(f"{p.upper()}_API_KEY")
           or (_os.environ.get(str(meta.get("key_env"))) if meta.get("key_env") else None)
           or (Config.LLM_API_KEY if p == str(Config.LLM_PROVIDER or "").lower() else None))
    return LLMClient(provider=p, api_key=key,
                     base_url=meta.get("default_base"),
                     model=meta.get("default_model"))


def _run_ensemble_draws(models: List[str], draw_fn: Any, min_count: int, *,
                        primary_provider: str, client_factory: Any,
                        skipped: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """ITEM 12：对每个（非主）副模型各跑一次同提示词二元抽取，返回 {provider: [binaries]}。

    ``draw_fn`` 即 ``extract_binary_forecasts`` 内的 ``_draw`` 闭包（``client=`` 指定副模型
    客户端，共用主抽取的提示词与切片）。与主提供方同名或重复的名字被去重跳过（主模型已抽过）。
    某副模型构造/抽取失败或产出空列表 → 记入 ``skipped`` 并继续（绝不阻断，degrade-safe）。"""
    secondary: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()
    prim = str(primary_provider or "").strip().lower()
    for name in (models or []):
        m = str(name or "").strip().lower()
        if not m or m == prim or m in seen:
            continue
        seen.add(m)
        try:
            client = client_factory(m)
            if client is None:
                skipped.append(m)
                continue
            drawn = draw_fn(min_count, [], client=client)
            if drawn:
                secondary[m] = drawn
            else:
                skipped.append(m)  # 空抽取（全被过滤）视同该模型无贡献
        except Exception as _de:  # noqa: BLE001 — 单个副模型失败绝不阻断集成/主抽取
            skipped.append(m)
            logger.warning(f"集成副模型 {m} 抽取失败（跳过）: {_de}")
    return secondary


def extract_binary_forecasts(report_markdown: str, llm, *, min_count: int = 10,
                             language: str = "English",
                             situation_brief: Optional[str] = None,
                             themes: Optional[List[str]] = None,
                             signal_pack: Optional[str] = None,
                             market_pack: Optional[str] = None,
                             markets: Optional[List[Dict[str, Any]]] = None,
                             ensemble_client_factory: Optional[Any] = None) -> Dict[str, Any]:
    """Extract/derive >=min_count INDEPENDENT binary forecasts from the dossier.

    Returns ``{"binary_forecasts": [...], "binary_quality": {...}}``. Degrade-safe:
    a malformed reply yields an empty list (caller keeps the scenario forecast). Runs a
    single bounded top-up pass if the first pass is short of ``min_count``.

    RPT-6: ``themes``（可选）取代硬编码的 mercantilism|ai|intersection 契约；未给出时
    提示词改用主题无关措辞、主题串自由。RPT-4（FORECAST_BINARY_CONTRARIAN，默认开）：
    要求近半数陈述以「证据支持概率 <0.5」的反共识方向表述，首轮 spread 过低/全部 >0.5
    时追加一次有界低概率重述补足。XRUN-1(b)（FORECAST_SIM_SENSITIVITY，默认开）：
    注入模拟量化信号包并要求每个概率说明其相对 base_rate_anchor 的位移与来源。
    预测市场（PREDICTION_MARKETS_ENABLED，默认开）：``market_pack``（渲染好的市场表）
    注入提示词要求对照市场隐含概率；``markets``（规整化快照）用于回填/校验 market_anchor。
    PM-2：抽取后跑一次确定性市场匹配（anchor_binaries_to_markets）+ 10pp 分歧有界重述
    （enforce_market_divergence），并把对照负载放进返回值 ``market_comparison``（供落
    market_comparison.json）。RQ-2：dossier 切片改为 head+tail（结论在文末）。
    """
    content = (report_markdown or "")
    _bbudget = int(_cfg("FORECAST_BINARY_EXTRACT_BUDGET", 48000))
    _bhr = _coerce_float(_cfg("FORECAST_EXTRACT_HEAD_RATIO", 0.6))
    content = slice_head_tail(content, _bbudget, _bhr if _bhr is not None else 0.6)
    themes = [str(t).strip().lower() for t in (themes or []) if str(t).strip()] or None
    contrarian = bool(_cfg("FORECAST_BINARY_CONTRARIAN", True))
    sim_sensitive = bool(_cfg("FORECAST_SIM_SENSITIVITY", True)) and bool((signal_pack or "").strip())
    market_aware = (bool(_cfg("PREDICTION_MARKETS_ENABLED", True))
                    and bool((market_pack or "").strip()))
    # market_id → 隐含概率查找表（用我们抓取的快照回填模型转录的锚点，不盲信模型数字）。
    market_lookup: Dict[str, float] = {}
    for m in (markets or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("market_id") or "").strip()
        ip = _coerce_float(m.get("implied_yes_prob"))
        if mid and ip is not None and 0.0 <= ip <= 1.0:
            market_lookup[mid] = ip

    def _draw(instr_min: int, exclude: List[str], *, low_p: bool = False,
              client: Any = None) -> List[Dict[str, Any]]:
        # ITEM 12：client 指定时用该（副模型）客户端抽取，否则用主 llm——集成各模型共用同一提示词。
        _llm = client if client is not None else llm
        user = _BINARY_FORECAST_INSTRUCTIONS.format(
            min_count=instr_min, language=language,
            theme_enum=("|".join(themes) if themes else _BINARY_DEFAULT_THEME_ENUM),
            tie_rule=(f"tied to {', '.join(themes)}" if themes else _BINARY_DEFAULT_TIE_RULE),
        )
        if contrarian:
            user += _BINARY_LOW_P_RULE if low_p else _BINARY_CONTRARIAN_RULE
        if sim_sensitive:
            user += (
                "\nSIMULATION SENSITIVITY: each adjustment_rationale MUST state how far and in "
                "which direction the probability moved from base_rate_anchor, citing the specific "
                "simulation signal below when one applies; set the source field to that signal "
                "name, or to \"research-prior\" when no simulation signal informs it."
            )
        if market_aware:
            user += _BINARY_MARKET_RULE
        if exclude:
            user += "\n\nDo NOT repeat these already-captured forecasts (produce NEW, distinct ones):\n" + \
                "\n".join(f"- {s}" for s in exclude[:30])
        if situation_brief:
            user += f"\n\n[Situation brief]\n{situation_brief[:2000]}"
        if sim_sensitive:
            user += f"\n\n[Simulation quantitative signals]\n{str(signal_pack)[:4000]}"
        if market_aware:
            # PM-2：市场表切片 4000→8000，让相关性门控后的更多市场进入锚定视野。
            user += f"\n\n[Prediction market signals]\n{str(market_pack)[:8000]}"
        user += f"\n\n[Research dossier]\n{content}"
        raw = _llm.chat_json(messages=[{"role": "user", "content": user}],
                             temperature=0.25, max_tokens=4096)
        items = raw.get("binary_forecasts") if isinstance(raw, dict) else None
        return _normalize_binaries(items or [], allowed_themes=themes,
                                   market_lookup=market_lookup or None)

    def _merge(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> None:
        seen = {_binary_key(b["statement"]) for b in base}
        for b in extra:
            k = _binary_key(b["statement"])
            if k not in seen:
                seen.add(k)
                base.append(b)

    binaries = _draw(min_count, [])
    if len(binaries) < min_count:
        need = min_count - len(binaries)
        _merge(binaries, _draw(need + 2, [b["statement"] for b in binaries]))
    # RPT-4：一次有界的低概率重述补足——首轮 stdev<0.12 或全部 >0.5 时，同方向 top-up
    # 永远无法过 conviction 门，必须显式索取 0.05-0.35 区间的反共识陈述。
    if contrarian and binaries:
        probs = [b["probability"] for b in binaries]
        mean = sum(probs) / len(probs)
        std = math.sqrt(sum((p - mean) ** 2 for p in probs) / len(probs))
        if std < 0.12 or min(probs) > 0.5:
            try:
                low_n = max(3, min_count // 3)
                _merge(binaries, _draw(low_n, [b["statement"] for b in binaries], low_p=True))
            except Exception as _le:  # noqa: BLE001 — 补足失败保留首轮结果（degrade-safe）
                logger.warning(f"二元预测低概率重述补足失败（忽略）: {_le}")
    # renumber ids stably F1..Fn（锚定用稳定 id 匹配，故在此之后再跑市场锚定）
    for i, b in enumerate(binaries, start=1):
        b["id"] = f"F{i}"
    # ITEM 12：多模型集成——主模型抽完后，对每个所列（非主）提供方各跑一次同提示词二元抽取，
    # 按 id/陈述匹配同一条预测，用与种子集成同一套 extremizing log-odds（ENSEMBLE_EXTREMIZE_A）
    # 把各模型概率池化为发布概率，记 binary['ensemble']={models,probs,pooled,spread}。置于市场锚定
    # **之前** ⇒ 锚定/分歧重述基于池化后的概率，market_anchor.divergence 一次算准。任一副提供方
    # 失败→跳过该模型并记 flag，绝不阻断；FORECAST_ENSEMBLE_MODELS 空=整段跳过（逐字节复现旧行为）。
    ensemble_low_agreement: List[str] = []
    ensemble_skipped: List[str] = []
    ensemble_pooled_models: List[str] = []
    _ens_models = [m.strip().lower() for m in str(_cfg("FORECAST_ENSEMBLE_MODELS", "") or "").split(",")
                   if m.strip()]
    if binaries and _ens_models:
        try:
            secondary = _run_ensemble_draws(
                _ens_models, _draw, min_count,
                primary_provider=str(getattr(llm, "provider", "") or "primary").lower(),
                client_factory=ensemble_client_factory or _build_ensemble_client,
                skipped=ensemble_skipped)
            if secondary:
                ensemble_pooled_models = sorted(secondary.keys())
                from .ensemble import pool_binary_forecasts as _pool
                a_raw = _cfg("ENSEMBLE_EXTREMIZE_A", None)
                try:
                    _a = float(a_raw) if a_raw is not None else None
                except (TypeError, ValueError):
                    _a = None
                _thr = _coerce_float(_cfg("FORECAST_ENSEMBLE_SPREAD_THRESHOLD", 0.15))
                ensemble_low_agreement = _pool(
                    binaries, secondary,
                    primary_model=str(getattr(llm, "provider", "") or "primary").lower(),
                    extremize_a=_a,
                    spread_threshold=_thr if _thr is not None else 0.15)
        except Exception as _ee:  # noqa: BLE001 — 集成为增强，绝不阻断二元抽取
            logger.warning(f"多模型预测集成失败（忽略，保留主模型结果）: {_ee}")
    # PM-2：确定性市场锚定 + 10pp 分歧有界重述 + 对照负载。任何失败 → 保留无锚点结果
    # （_normalize_binaries 已回填的模型自愿锚点仍在），即今日行为（degrade-safe）。
    market_comparison: Optional[Dict[str, Any]] = None
    if binaries and (markets or []):
        try:
            anchor_binaries_to_markets(binaries, markets, llm, language=language)
            enforce_market_divergence(binaries, llm, language=language)
            market_comparison = build_market_comparison(binaries)
        except Exception as _ae:  # noqa: BLE001 — 锚定为增强，绝不阻断二元抽取
            logger.warning(f"预测市场锚定失败（忽略，保留无锚点结果）: {_ae}")
            market_comparison = None
    out: Dict[str, Any] = {
        "binary_forecasts": binaries,
        "binary_quality": _binary_quality(binaries, min_count=min_count,
                                          themes_expected=themes),
    }
    # ITEM 12：把多模型集成诊断落进 binary_quality（该块经 report_agent 落到 forecast['binary_quality']
    # 并被 render_binary_forecasts_block 消费）。仅当 FORECAST_ENSEMBLE_MODELS 非空时附加（默认整段不加
    # → schema 逐字节不变）。low_agreement 收 spread>阈值的预测 id，其陈述在报表以 ±spread 高亮分歧；
    # skipped 记构造/抽取失败被跳过的副提供方；pooled_models 记真正参与池化的副提供方。
    if _ens_models:
        _ens_block: Dict[str, Any] = {
            "enabled_models": _ens_models,
            "pooled_models": ensemble_pooled_models,
            "skipped": ensemble_skipped,
            "low_agreement": ensemble_low_agreement,
            "spread_threshold": (_coerce_float(_cfg("FORECAST_ENSEMBLE_SPREAD_THRESHOLD", 0.15)) or 0.15),
        }
        _bq = out["binary_quality"]
        if isinstance(_bq, dict):
            _bq["ensemble"] = _ens_block
            if ensemble_low_agreement:
                _bq.setdefault("issues", []).append(
                    f"{len(ensemble_low_agreement)} forecast(s) show cross-model disagreement "
                    f"(spread > {_ens_block['spread_threshold']}): {', '.join(ensemble_low_agreement)}")
    if market_comparison and market_comparison.get("comparisons"):
        out["market_comparison"] = market_comparison
    return out


# ------------------------------------------ requirement-horizon consistency (RQ-6)
# 一个 2026 年期中选举 brief 抽出的二元预测若全部落在 2028 年结算 = 交付物答非所问。
# requirement_spec 不解析目标日期（只有 themes/page_budget 等），这里直接用正则从需求
# 文本抽取「目标年份」，与二元预测的结算年份（horizon_year 字段 + 陈述/判定标准中出现
# 的年份）对比；两侧都解析出年份且**完全不相交**时，在 forecast['quality'] 合并写入
# ``horizon_mismatch`` 标记（镜像 _apply_publish_gate 的 merge 行为，绝不覆盖既有
# quality 键），供发布门（RQ-6 gate 半）降级 confidence。Degrade-safe：任一侧解析不出
# 年份 → 不加标记；任何异常 → 原 forecast 原样返回。
_REQ_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _plausible_years(text: Any, lo: int, hi: int) -> set:
    """Years mentioned in ``text`` that fall inside the plausible window [lo, hi]."""
    out: set = set()
    for m in _REQ_YEAR_RE.finditer(str(text or "")):
        y = int(m.group(1))
        if lo <= y <= hi:
            out.add(y)
    return out


def parse_requirement_years(requirement_text: Optional[str], *,
                            now_year: Optional[int] = None) -> List[int]:
    """Extract the brief's candidate TARGET years from the requirement text (sorted).

    过滤到 [now-1, now+30] 窗口：历史背景年份（"since the 2008 crisis"）不是结算目标，
    保留会造成假阳性。Pure / offline；解析不出 → []（调用方随之不加标记，degrade-safe）。
    """
    if now_year is None:
        from datetime import datetime
        now_year = datetime.now().year
    return sorted(_plausible_years(requirement_text, now_year - 1, now_year + 30))


def apply_horizon_consistency(forecast: Dict[str, Any], requirement_text: Optional[str], *,
                              now_year: Optional[int] = None) -> Dict[str, Any]:
    """RQ-6（extractor 半）：抽取时校验二元预测结算年份与 brief 目标年份的一致性。

    需求文本目标年份集合与二元预测结算年份集合均非空且**无交集**时（例：2026 brief 抽出
    全 2028 结算且无一条 2026 的二元），把结构化标记 merge 进 ``forecast['quality']``
    （绝不覆盖既有键——镜像 _apply_publish_gate 的 merge 行为），供发布门降信心。
    Mutates + returns ``forecast``。FORECAST_HORIZON_CHECK 旗标关闭、年份解析不出、
    或任何异常 → 原样返回（degrade-safe，不阻断抽取）。
    """
    try:
        if not isinstance(forecast, dict) or not _cfg("FORECAST_HORIZON_CHECK", True):
            return forecast
        if now_year is None:
            from datetime import datetime
            now_year = datetime.now().year
        req_years = set(parse_requirement_years(requirement_text, now_year=now_year))
        if not req_years:
            return forecast  # brief 未给出可解析的目标年份 → 无从比对（不加标记）
        lo, hi = now_year - 1, now_year + 30
        bin_years: set = set()
        for b in (forecast.get("binary_forecasts") or []):
            if not isinstance(b, dict):
                continue
            hy = _coerce_float(b.get("horizon_year"))
            if hy is not None and lo <= int(hy) <= hi:
                bin_years.add(int(hy))
            bin_years |= _plausible_years(
                f"{b.get('statement') or ''} {b.get('resolution_criteria') or ''}", lo, hi)
        if not bin_years or (req_years & bin_years):
            return forecast  # 二元侧无年份可比 / 存在交集 → 视为一致，不加标记
        req_s, bin_s = sorted(req_years), sorted(bin_years)
        _q0 = forecast.get("quality")
        quality = dict(_q0) if isinstance(_q0, dict) else {}
        quality["horizon_mismatch"] = {
            "requirement_years": req_s,
            "binary_years": bin_s,
            "detail": (f"binary forecasts resolve in {'/'.join(map(str, bin_s))} but the "
                       f"brief targets {'/'.join(map(str, req_s))} (no overlap)"),
        }
        forecast["quality"] = quality
        return forecast
    except Exception as _he:  # noqa: BLE001 — 一致性检查绝不阻断抽取（degrade-safe）
        logger.warning(f"需求-预测时间范围一致性检查失败（忽略）: {_he}")
        return forecast


# ----------------------------------------------------------- forecast spine (P0-1)
_SPINE_INSTRUCTIONS = """你是预测校准专家。在撰写任何叙事之前，先基于下面的【研究输入】（参考类基率/
驱动因素/观察指标/候选情景）与【模拟量化信号】，给出一个**机器可读**的结构化预测骨架。
只输出 JSON，不要解释。字段：
{
  "headline": "一句话核心结论",
  "horizon": "预测时间范围（如 '2030'）",
  "scenarios": [
    {
      "name": "情景名",
      "probability": 0.0-1.0,         // 所有情景概率之和必须≈1
      "summary": "该情景的简述",
      "key_drivers": ["关键驱动因素", ...],
      "base_rate_anchor": "该情景的参考类基率/外部视角起点（先从基率出发）",
      "adjustment_rationale": "据本案具体特征对基率所做调整及理由（anchor-and-adjust）",
      "resolution_criteria": "可证伪的判定标准：到期时如何客观判断此情景是否发生（含可观测指标/阈值/日期）"
    }
  ],
  "key_uncertainties": ["最大的不确定性来源", ...],
  "confidence": "low|medium|high",
  "confidence_rationale": "信心评级的理由（数据质量/分歧度/时间跨度）"
}
要求：2-5 个**互斥且尽量穷尽（MECE）**的情景，并**必须**含一个「维持现状/其它」兜底情景；
概率为数值且之和≈1；resolution_criteria 必须客观可验证。**对每个情景采用 anchor-and-adjust**：
先给 base_rate_anchor（参考类基率/外部视角），再据案例特征调整得到最终 probability，并在
adjustment_rationale 说明，以抵御基率忽视/内视过度自信。先确定数字与判定标准，再让叙事去捍卫它们。"""


def _spine_draw(llm, user: str, temperature: float, max_tokens: int) -> Dict[str, Any]:
    """One spine LLM draw → assembled forecast dict (degrade-safe on bad replies)."""
    raw = llm.chat_json(
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not isinstance(raw, dict):
        raw = {}
    return _assemble_forecast(raw)


def _pool_spine_draws(draws: List[Dict[str, Any]], floor: float) -> Dict[str, Any]:
    """R2-CAL-1/R2-CAL-17: pool K self-consistency draws (shared scenario names).

    The first draw establishes the canonical scenario set (names + criteria + anchors);
    each scenario's published probability becomes the MEAN across the draws that
    surfaced it, with [p_low, p_high] carrying the across-draw spread. Means are
    floored + renormalized. Confidence is demoted when the mean spread is wide.
    """
    from .ensemble import _norm_name  # local: avoid import cycle at module load
    base = draws[0]
    base_scen = [s for s in (base.get("scenarios") or []) if isinstance(s, dict)]
    # collect probabilities per canonical name across all draws
    probs_by_key: Dict[str, List[float]] = {}
    for d in draws:
        for s in (d.get("scenarios") or []):
            if not isinstance(s, dict):
                continue
            k = _norm_name(s.get("name"))
            if not k:
                continue
            p = _coerce_float(s.get("probability"))
            if p is not None:
                probs_by_key.setdefault(k, []).append(p)
    pooled: List[Dict[str, Any]] = []
    spreads: List[float] = []
    for s in base_scen:
        k = _norm_name(s.get("name"))
        ps = probs_by_key.get(k) or [_coerce_float(s.get("probability")) or 0.0]
        mean = sum(ps) / len(ps)
        if len(ps) > 1:
            m = mean
            sd = math.sqrt(sum((x - m) ** 2 for x in ps) / (len(ps) - 1))
        else:
            sd = 0.0
        spreads.append(sd)
        row = dict(s)
        row["probability"] = mean
        row["p_low"] = max(0.0, mean - sd)
        row["p_high"] = min(1.0, mean + sd)
        row["self_consistency_n"] = len(ps)
        pooled.append(row)
    # floor + renormalize the pooled means (keep intervals proportional)
    total = sum(r["probability"] for r in pooled)
    if total > 0:
        for r in pooled:
            r["probability"] = r["probability"] / total
    if floor > 0 and pooled:
        for r in pooled:
            if r["probability"] < floor:
                r["probability"] = floor
        t2 = sum(r["probability"] for r in pooled)
        if t2 > 0:
            for r in pooled:
                r["probability"] = r["probability"] / t2
    for r in pooled:
        r["probability"] = round(r["probability"], 4)
        r["p_low"] = round(min(r["p_low"], r["probability"]), 4)
        r["p_high"] = round(max(r["p_high"], r["probability"]), 4)
    out = dict(base)
    out["scenarios"] = pooled
    out["self_consistency_k"] = len(draws)
    out["self_consistency_mean_spread"] = round(sum(spreads) / len(spreads), 4) if spreads else 0.0
    # demote confidence on wide disagreement across draws (overconfidence guard)
    mean_spread = out["self_consistency_mean_spread"]
    if mean_spread >= 0.15 and out.get("confidence") == "high":
        out["confidence"] = "medium"
    if mean_spread >= 0.25 and out.get("confidence") == "medium":
        out["confidence"] = "low"
    return out


def derive_forecast_spine(llm, *, central_question: str = "", horizon: str = "",
                          situation_brief: Optional[str] = None,
                          forecast_inputs: str = "", signal_pack: str = "",
                          base_distribution: Optional[Dict[str, float]] = None,
                          quantitative_facts: str = "",
                          market_block: str = "") -> Dict[str, Any]:
    """NEXTSTEPS P0-1: derive the structured forecast *spine* from research +
    simulation SIGNALS — *before* any prose is written.

    This forces MECE probabilistic discipline up front and yields a spine whose
    scenarios+probabilities+resolution_criteria each later report section must
    defend, rather than reverse-engineering numbers out of finished narrative.
    Same output shape as ``extract_structured_forecast`` plus ``derived_from='spine'``.
    Degrade-safe: a malformed reply yields a well-formed empty forecast; the caller
    wraps in try/except and falls back to the post-hoc extractor.

    Optional enhancements (all inert unless their flag + input are supplied):
      * ``base_distribution`` — WorldState.shares anchor (R2-CAL-3) used to constrain
        the scenario set + probability band and to compute per-scenario divergence
        (R2-CAL-18); echoed into the output as ``base_distribution``.
      * ``quantitative_facts`` — S-tier metric digest (R2-CAL-16) requiring anchors to
        cite a metric + as-of date.
      * ``REPORT_SPINE_SELFCONSISTENCY_K`` — K self-consistency draws pooled to
        mean + spread (R2-CAL-1 / R2-CAL-17).
    """
    # R2-DETAIL-3：每块输入上限可配置。RQ-4：signal/inputs 两块 4000→6000（骨架情景/概率
    # 由这两块驱动，4000 会把驱动因素与量化信号截断，让骨架欠地气）；brief/facts 维持旧值。
    cap_brief = int(_cfg("REPORT_SPINE_INPUT_CAP_BRIEF", 2000))
    cap_inputs = int(_cfg("REPORT_SPINE_INPUT_CAP_INPUTS", 6000))
    cap_signal = int(_cfg("REPORT_SPINE_INPUT_CAP_SIGNAL", 6000))
    cap_facts = int(_cfg("REPORT_SPINE_INPUT_CAP_FACTS", 3000))
    user = _SPINE_INSTRUCTIONS
    if central_question:
        user += f"\n\n[核心问题]\n{central_question[:600]}"
    if horizon:
        user += f"\n\n[预测时间范围]\n{horizon[:120]}"
    if situation_brief:
        user += f"\n\n[态势简报]\n{situation_brief[:cap_brief]}"
    if forecast_inputs:
        user += f"\n\n[研究输入：参考类基率 / 驱动因素 / 观察指标 / 候选情景]\n{forecast_inputs[:cap_inputs]}"
    if signal_pack:
        user += f"\n\n[模拟量化信号]\n{signal_pack[:cap_signal]}"
    # 预测市场校准锚点（Polymarket 公开 Gamma API）：市场隐含概率是外部视角的
    # 聚合信念——与所列市场重叠的情景概率应对照之，偏离 >10 个百分点须在
    # adjustment_rationale 说明依据（市场是校准锚点，不是真值）。空串时提示词不变。
    if market_block:
        # PM-2：市场块切片 2500→6000，让相关性门控后的更多市场进入骨架校准视野。
        user += ("\n\n[预测市场隐含概率（Polymarket 实盘·校准锚点，非真值）]\n"
                 + str(market_block)[:6000]
                 + "\n与上述市场重叠的情景，其概率须对照市场隐含概率；偏离超过 10 个百分点时"
                   "在 adjustment_rationale 中显式解释分歧（市场遗漏/错价了什么）。")
    # R2-CAL-16：S 级量化事实数字底座，要求锚点引用 指标 + as_of 日期。
    if quantitative_facts:
        user += ("\n\n[S级量化事实（数字底座，base_rate_anchor 须引用其中的 指标+as_of 日期）]\n"
                 + str(quantitative_facts)[:cap_facts])
    # R2-CAL-3：把模拟得到的 WorldState.shares 作为基准分布锚点，约束情景集合与概率带。
    anchor_ws = bool(_cfg("REPORT_SPINE_ANCHOR_WORLDSTATE", False)) and isinstance(base_distribution, dict)
    if anchor_ws and base_distribution:
        shares_txt = "；".join(f"{k}={float(v):.2f}" for k, v in base_distribution.items()
                              if _coerce_float(v) is not None)
        if shares_txt:
            user += ("\n\n[基准分布锚点（模拟 WorldState 份额，先验）]\n" + shares_txt
                     + "\n请以此为外部视角先验：沿用相同情景集合，最终概率应落在各自份额的合理带内"
                       "（偏离须在 adjustment_rationale 中给出具体证据）。")

    max_tokens = int(_cfg("REPORT_SPINE_MAX_TOKENS", 6144))  # R2-CAL-11: 2048→6144
    floor = _coerce_float(_cfg("FORECAST_PROB_FLOOR", 0.0)) or 0.0
    try:
        k = int(_cfg("REPORT_SPINE_SELFCONSISTENCY_K", 1) or 1)
    except (TypeError, ValueError):
        k = 1
    k = max(1, k)

    first = _spine_draw(llm, user, 0.2, max_tokens)
    if not first.get("scenarios"):
        # R2-CAL-11：骨架为空 → 告警并重试一次后再让上层回退成稿后抽取。
        logger.warning("预测骨架首轮无情景，重试一次")
        first = _spine_draw(llm, user, 0.2, max_tokens)

    draws = [first]
    if k > 1 and first.get("scenarios"):
        names = [str(s.get("name")) for s in first["scenarios"] if isinstance(s, dict)]
        follow = user + ("\n\n[已确定情景集合：请沿用完全相同的情景名，仅独立重新估计各自概率"
                         "（其和≈1），不要新增或重命名情景]\n" + "；".join(names))
        for i in range(1, k):
            temp = min(0.9, 0.2 + 0.15 * i)  # varied temperature for diversity
            d = _spine_draw(llm, follow, temp, max_tokens)
            if d.get("scenarios"):
                draws.append(d)

    out = _pool_spine_draws(draws, floor) if len(draws) > 1 else first
    out["derived_from"] = "spine"

    # R2-CAL-3 echo + R2-CAL-18 per-scenario model-vs-sim divergence.
    if anchor_ws and base_distribution and out.get("scenarios"):
        from .ensemble import _norm_name
        bd_keyed = {_norm_name(k2): _coerce_float(v) for k2, v in base_distribution.items()}
        out["base_distribution"] = {str(k2): float(v) for k2, v in base_distribution.items()
                                    if _coerce_float(v) is not None}
        divergences = []
        for s in out["scenarios"]:
            ws = bd_keyed.get(_norm_name(s.get("name")))
            if ws is None:
                continue
            d = abs((_coerce_float(s.get("probability")) or 0.0) - ws)
            s["worldstate_divergence"] = round(d, 4)
            thin = (s.get("self_consistency_n", 1) or 1) <= 1
            if d >= 0.2:
                # widen the published interval & demote confidence on thin-evidence divergence.
                lo = _coerce_float(s.get("p_low"))
                hi = _coerce_float(s.get("p_high"))
                p = _coerce_float(s.get("probability")) or 0.0
                s["p_low"] = round(max(0.0, min(lo if lo is not None else p, p) - d / 2), 4)
                s["p_high"] = round(min(1.0, max(hi if hi is not None else p, p) + d / 2), 4)
                if thin:
                    divergences.append({"name": str(s.get("name")), "divergence": round(d, 4)})
        if divergences:
            out.setdefault("quality", {})["model_vs_sim_divergence"] = divergences
            if out.get("confidence") == "high":
                out["confidence"] = "medium"
    return out


def render_forecast_spine_block(forecast: Optional[Dict[str, Any]], max_scenarios: int = 6) -> str:
    """Render a compact, authoritative spine block to pin into each section prompt.

    Empty forecast / no scenarios → "" (so the prefix injection is a no-op and the
    section prompt is byte-identical to the pre-spine path). NEXTSTEPS P0-1.
    """
    if not isinstance(forecast, dict):
        return ""
    scenarios = forecast.get("scenarios") or []
    if not scenarios:
        return ""
    lines = [
        "【预测骨架（权威·先于叙事确定，本章撰写须对齐并捍卫所分配的概率）】",
    ]
    headline = str(forecast.get("headline") or "").strip()
    horizon = str(forecast.get("horizon") or "").strip()
    if headline:
        lines.append(f"核心结论：{headline}" + (f"（时间范围 {horizon}）" if horizon else ""))
    for s in scenarios[:max_scenarios]:
        if not isinstance(s, dict):
            continue
        try:
            pct = f"{float(s.get('probability') or 0.0) * 100:.0f}%"
        except (TypeError, ValueError):
            pct = "—"
        name = str(s.get("name") or "未命名情景")
        crit = str(s.get("resolution_criteria") or "").strip()
        line = f"· [{pct}] {name}"
        if crit:
            line += f" — 判定：{crit[:160]}"
        lines.append(line)
    conf = str(forecast.get("confidence") or "").strip()
    if conf:
        lines.append(f"整体信心：{conf}")
    lines.append(
        "撰写本章时：凡涉及上述情景的论断须与所分配概率一致；若本章证据要求调整某情景概率，"
        "请显式说明依据，不得无声偏离骨架。"
    )
    return "\n".join(lines)


def _esc_cell(x: Any) -> str:
    """markdown 表格单元转义（管道符/换行）。"""
    return str(x).replace("|", "／").replace("\n", " ").strip()


def render_binary_forecasts_block(forecast: Optional[Dict[str, Any]],
                                  language: str = "English") -> str:
    """QUALITY-OPT B1: render a DETERMINISTIC 'Part 1 — Binary Forecasts' table.

    Deterministic (no LLM) so the brief's headline deliverable always appears in the report
    AND always matches forecast.json exactly (kills prose↔json probability drift). Empty
    binary list → "" (degrade-safe: nothing injected). Bilingual headers track output language.
    """
    if not isinstance(forecast, dict):
        return ""
    binaries = forecast.get("binary_forecasts") or []
    if not binaries:
        return ""
    zh = not str(language or "").lower().startswith("en")
    # 预测市场锚点列：任一预测带 market_anchor 时追加「市场隐含 P(yes)」列（预测 vs 市场
    # 对照）；全无锚点时不加列，表格与历史逐字节一致（degrade-safe）。
    has_anchor = any(isinstance(b, dict) and isinstance(b.get("market_anchor"), dict)
                     for b in binaries)
    if zh:
        head = "## 第一部分 · 二元预测（Part 1 — Binary Forecasts）"
        intro = ("以下为可独立判定的二元（是/否）预测，每条含概率与客观判定标准"
                 "（指标·阈值·日期·来源）。概率反映真实研判，非对冲。")
        cols = "| # | 预测（一句话） | 概率 | 判定标准 | 主题 |"
        anchor_col = " 市场隐含 P(yes)（Polymarket） |"
    else:
        head = "## Part 1 — Binary Forecasts"
        intro = ("Independent binary (yes/no) forecasts, each with a probability and an "
                 "objective resolution test (metric · threshold · date · source). "
                 "Probabilities express genuine conviction, not hedging.")
        cols = "| # | Forecast (one sentence) | Prob. | Resolution criteria | Theme |"
        anchor_col = " Market P(yes) (Polymarket) |"
    sep = "|---|---|---|---|---|"
    if has_anchor:
        cols += anchor_col
        sep += "---|"
        intro += ("其中标注市场隐含概率的预测可与真实预测市场对照（市场为校准锚点，非真值）。"
                  if zh else
                  " Forecasts with a market-implied probability are benchmarked against live "
                  "prediction markets (markets are calibration anchors, not ground truth).")
    lines = [head, "", intro, "", cols, sep]
    # ITEM 12：多模型集成开启时，概率列以 ±spread（跨模型样本 stdev）显示分歧；spread 超阈值
    # （binary_quality.ensemble.spread_threshold，缺省 0.15）的预测加 ⚠ 低一致性标记。无 ensemble
    # 块（默认单模型）→ 概率列逐字节不变（degrade-safe）。
    _ens_meta = (forecast.get("binary_quality") or {}).get("ensemble") if isinstance(
        forecast.get("binary_quality"), dict) else None
    _ens_thr = _coerce_float((_ens_meta or {}).get("spread_threshold"))
    if _ens_thr is None:
        _ens_thr = 0.15
    for b in binaries:
        if not isinstance(b, dict):
            continue
        try:
            pct = f"{float(b.get('probability') or 0.0) * 100:.0f}%"
        except (TypeError, ValueError):
            pct = "—"
        _ens = b.get("ensemble")
        if isinstance(_ens, dict):
            _spread = _coerce_float(_ens.get("spread"))
            if _spread is not None and _spread > 0:
                pct += f" ±{_spread * 100:.0f}%"
                if _spread > _ens_thr:
                    pct += " ⚠"
        line = "| {id} | {st} | {p} | {rc} | {th} |".format(
            id=_esc_cell(b.get("id") or ""),
            st=_esc_cell(b.get("statement") or ""),
            p=pct,
            # WAVE9：去掉 [:200] 硬截断——判定标准被切成 'used in Q3 202' 型断句是交付缺陷；
            # _esc_cell 已转义管道符/换行，长单元格由渲染端自然换行。
            rc=_esc_cell(str(b.get("resolution_criteria") or "")),
            th=_esc_cell(b.get("theme") or ""),
        )
        if has_anchor:
            anchor = b.get("market_anchor")
            cell = "—"
            if isinstance(anchor, dict):
                ip = _coerce_float(anchor.get("implied_yes_prob"))
                dv = _coerce_float(anchor.get("divergence"))
                if ip is not None:
                    cell = f"{ip * 100:.0f}%"
                    if dv is not None:
                        cell += f" (Δ{dv * 100:+.0f}pt)"
            line += f" {_esc_cell(cell)} |"
        lines.append(line)
    q = forecast.get("binary_quality") or {}
    if q:
        if zh:
            lines += ["", (f"_共 {q.get('count')} 条；高研判（≥70% 或 ≤30%）{q.get('conviction_count')} 条；"
                           f"客观判定 {q.get('sharp_criteria_count')} 条。_")]
        else:
            lines += ["", (f"_{q.get('count')} forecasts; {q.get('conviction_count')} high-conviction "
                           f"(≥70% or ≤30%); {q.get('sharp_criteria_count')} with objective criteria._")]
    # ITEM 12：集成低一致性脚注——列出跨模型 spread 超阈值的预测 id（±spread 已在概率列高亮）。
    _ens_meta2 = q.get("ensemble") if isinstance(q, dict) else None
    if isinstance(_ens_meta2, dict) and _ens_meta2.get("low_agreement"):
        _la = ", ".join(str(x) for x in _ens_meta2.get("low_agreement") or [])
        _pm = ", ".join(str(x) for x in _ens_meta2.get("pooled_models") or [])
        if zh:
            lines += ["", (f"_多模型集成（{_pm}）：{_la} 存在跨模型分歧（±spread 超 "
                           f"{_ens_meta2.get('spread_threshold')}，概率列以 ⚠ 标记，谨慎解读）。_")]
        else:
            lines += ["", (f"_Multi-model ensemble ({_pm}): {_la} show cross-model disagreement "
                           f"(spread > {_ens_meta2.get('spread_threshold')}, flagged ⚠ in the "
                           f"probability column — interpret with caution)._")]
    return "\n".join(lines)


def render_resolution_block(forecast: Optional[Dict[str, Any]],
                            indicators: Optional[List[Dict[str, Any]]] = None,
                            language: str = "Chinese") -> str:
    """NEXTSTEPS P2-2: 渲染一个**确定性**的「如何验证本预测」章节。

    逐情景列出可证伪的判定标准 + 来自 forecast_inputs 的带日期/触发型观察指标（并把指标绑定到
    它所判别的情景）。一个没有明确、可观测、带日期指标的预测无法被追踪或打分——这正是"利率可能
    上升" vs "若指标 X 于日期 Z 前超过 Y，则情景 A 确认"的区别。forecast 无情景 → ""（不追加）。

    WAVE9：新增 language 参数（默认 "Chinese"，与历史输出逐字节一致）——此前标题/表头硬编码
    中文，英文报告末尾出现整段中文章节。调用方（report_agent）传入报告输出语言。
    """
    if not isinstance(forecast, dict):
        return ""
    scenarios = forecast.get("scenarios") or []
    if not scenarios:
        return ""
    zh = not str(language or "").strip().lower().startswith("en")
    if zh:
        lines = [
            "## 如何验证本预测（判定标准与观察指标）",
            "本节给出每个情景**可证伪、可追踪**的判定标准与到期/触发型观察指标，供日后核对与校准。",
            "",
            "### 各情景判定标准",
        ]
    else:
        lines = [
            "## How to Verify This Forecast (Resolution Criteria & Indicators)",
            "This section lists **falsifiable, trackable** resolution criteria for each scenario, "
            "plus dated/triggered indicators for future scoring and calibration.",
            "",
            "### Per-Scenario Resolution Criteria",
        ]
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        try:
            pct = f"{float(s.get('probability') or 0.0) * 100:.0f}%"
        except (TypeError, ValueError):
            pct = "—"
        name = str(s.get("name") or ("未命名情景" if zh else "Unnamed scenario"))
        crit = str(s.get("resolution_criteria") or "").strip() or (
            "（缺明确判定标准——需补全）" if zh
            else "(no explicit resolution criteria — needs completion)")
        sep = "：" if zh else ": "
        lines.append(f"- **[{pct}] {name}**{sep}{crit}")
    inds = [i for i in (indicators or []) if isinstance(i, dict)]
    if inds:
        lines.append("")
        if zh:
            lines.append("### 观察指标（到期/触发即核对）")
            lines.append("| 指标 | 到期/触发 | 关联情景 |")
        else:
            lines.append("### Indicators to Watch (check at expiry/trigger)")
            lines.append("| Indicator | Due / trigger | Discriminates scenario |")
        lines.append("|---|---|---|")
        for i in inds[:20]:
            name = _esc_cell(i.get("indicator") or i.get("name") or i.get("metric") or "—")
            trig = _esc_cell(i.get("date_or_trigger") or i.get("date") or i.get("trigger") or "—")
            disc = _esc_cell(i.get("discriminates") or i.get("scenario") or "—")
            lines.append(f"| {name or '—'} | {trig or '—'} | {disc or '—'} |")
    return "\n".join(lines)


_CRITIQUE_INSTRUCTIONS = """你是预测红队评审。下面是一个结构化预测对象（JSON）。请审查并修正它，重点检查：
1) 过度自信：是否有情景概率过高而证据不足？向不确定性回归（base-rate）。
2) 基率忽视：是否忽略了历史基率/惯性情景？
3) 无支撑的跳跃：resolution_criteria 是否客观可验证？
4) 互斥穷尽：情景是否互斥、是否需要补一个「其它/维持现状」兜底情景？
输出修正后的**同样结构**的 JSON（headline/horizon/scenarios[name,probability,summary,key_drivers,resolution_criteria]/
key_uncertainties/confidence/confidence_rationale），并在每个情景加一个 "critique_note" 字段说明你的调整。
只输出 JSON。概率之和应≈1。"""


def self_critique_forecast(forecast: Dict[str, Any], llm) -> Dict[str, Any]:
    """Red-team + recalibrate a structured forecast (EXECPLAN2 I-3-5).

    Runs one adversarial LLM pass that pushes back on overconfidence / base-rate
    neglect / unsupported leaps and may add a status-quo fallback scenario, then
    re-normalizes. Returns a new forecast dict tagged ``critiqued=True``; on any
    failure returns the input unchanged (degrade-safe).
    """
    import json as _json
    try:
        raw = llm.chat_json(
            messages=[{"role": "user",
                       "content": _CRITIQUE_INSTRUCTIONS + "\n\n[预测对象]\n" + _json.dumps(forecast, ensure_ascii=False)}],
            temperature=0.2,
            max_tokens=2048,
        )
        if not isinstance(raw, dict) or not raw.get("scenarios"):
            return forecast
        out = dict(forecast)
        out["scenarios"] = _normalize_scenarios(raw.get("scenarios"))
        # preserve critique_note per scenario if the model supplied it
        for new_s, raw_s in zip(out["scenarios"], raw.get("scenarios") or []):
            if isinstance(raw_s, dict) and raw_s.get("critique_note"):
                new_s["critique_note"] = str(raw_s["critique_note"])
        if raw.get("confidence"):
            c = str(raw["confidence"]).lower()
            if c in ("low", "medium", "high"):
                out["confidence"] = c
        if raw.get("confidence_rationale"):
            out["confidence_rationale"] = str(raw["confidence_rationale"])
        # R2-CAL-8：让红队评审「谦逊单调」——修正只能降低、不得抬高峰值自信。若评审反而把
        # 最高概率推得比原来更高，则把峰值夹回原始上限并重新归一（cap residual growth）。
        out["scenarios"] = _enforce_humility_monotone(forecast, out["scenarios"])
        out["critiqued"] = True
        return out
    except Exception:
        return forecast


def _enforce_humility_monotone(orig: Dict[str, Any],
                               new_scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clamp a critiqued scenario set so its peak probability never EXCEEDS the
    original peak (a red-team pass must not manufacture more confidence). Renormalizes
    after clamping. Pure; returns the (possibly) adjusted list. R2-CAL-8.
    """
    orig_probs = [_coerce_float(s.get("probability")) or 0.0
                  for s in (orig.get("scenarios") or []) if isinstance(s, dict)]
    if not orig_probs or len(new_scenarios) < 2:
        return new_scenarios
    orig_max = max(orig_probs)
    peak = max(new_scenarios, key=lambda s: _coerce_float(s.get("probability")) or 0.0)
    cur_max = _coerce_float(peak.get("probability")) or 0.0
    if cur_max <= orig_max + 1e-9:
        return new_scenarios
    # Cap the peak at the original peak and redistribute the FREED mass across the
    # other scenarios proportionally (a plain renormalize would re-inflate the peak).
    excess = cur_max - orig_max
    others = [s for s in new_scenarios if s is not peak]
    other_total = sum((_coerce_float(s.get("probability")) or 0.0) for s in others)
    peak["probability"] = round(orig_max, 4)
    if other_total > 0:
        for s in others:
            p = _coerce_float(s.get("probability")) or 0.0
            s["probability"] = round(p + excess * (p / other_total), 4)
    return new_scenarios


_PREMORTEM_INSTRUCTIONS = """你是预测红队的「事前验尸（pre-mortem）」分析师。假设到期时这份预测被证明**严重错误**。
请回答：(1) 最可能是哪个被低估的情景实际发生了？(2) 我们忽略了哪些信号/基率/尾部风险？
(3) 哪个情景的概率显得过度自信、应当向不确定性回归？只输出 JSON：
{ "underweighted_scenario": "情景名（须取自给定情景集合）", "missed_signals": ["...", ...],
  "overconfident_scenario": "情景名（可空）" }"""


def premortem_forecast(forecast: Dict[str, Any], llm) -> Dict[str, Any]:
    """R2-CAL-8 pre-mortem: imagine the forecast failed badly, surface missed signals,
    and gently widen uncertainty (append key_uncertainties + shave the overconfident
    peak toward the underweighted scenario, bounded). Gated by REPORT_PREMORTEM; on any
    failure returns the input unchanged (degrade-safe).
    """
    if not _cfg("REPORT_PREMORTEM", False):
        return forecast
    import json as _json
    try:
        scenarios = [s for s in (forecast.get("scenarios") or []) if isinstance(s, dict)]
        if not scenarios:
            return forecast
        raw = llm.chat_json(
            messages=[{"role": "user",
                       "content": _PREMORTEM_INSTRUCTIONS + "\n\n[预测对象]\n"
                       + _json.dumps(forecast, ensure_ascii=False)}],
            temperature=0.3,
            max_tokens=1024,
        )
        if not isinstance(raw, dict):
            return forecast
        out = dict(forecast)
        out["scenarios"] = [dict(s) for s in scenarios]
        from .ensemble import _norm_name
        missed = [str(x) for x in (raw.get("missed_signals") or []) if x]
        if missed:
            ku = list(out.get("key_uncertainties") or [])
            for m in missed:
                if m not in ku:
                    ku.append(m)
            out["key_uncertainties"] = ku[:12]
        # bounded transfer: shave up to 0.05 off the overconfident peak toward the
        # underweighted scenario, then renormalize (humility, capped residual growth).
        over = _norm_name(raw.get("overconfident_scenario"))
        under = _norm_name(raw.get("underweighted_scenario"))
        if over and under and over != under:
            src = next((s for s in out["scenarios"] if _norm_name(s.get("name")) == over), None)
            dst = next((s for s in out["scenarios"] if _norm_name(s.get("name")) == under), None)
            if src and dst:
                p_src = _coerce_float(src.get("probability")) or 0.0
                shift = min(0.05, p_src * 0.25)
                src["probability"] = round(p_src - shift, 4)
                dst["probability"] = round((_coerce_float(dst.get("probability")) or 0.0) + shift, 4)
        out["premortem"] = {"underweighted_scenario": str(raw.get("underweighted_scenario") or ""),
                            "missed_signals": missed[:8]}
        return out
    except Exception:
        return forecast


# ---------------------------------------------------------------- citation audit
# A "quantitative claim" = a sentence/line carrying a number or percentage. We
# check whether each such line is near a citation marker ([S1], 【S3】, etc.).
_CITATION_RE = re.compile(r"[\[【]\s*S\d+\s*[\]】]", re.I)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*%|\b\d{2,}(?:\.\d+)?\b|\b\d{4}年")
# REPORT-6：模拟/图谱接地标记也算作有效接地——agent 言行引用（> "…"）、因果边渲染
# （--[REL, …]-->）、以及 [sim_id]/【模拟】/【图谱】/[E\d] 等模拟与边引用标记。
_SIM_GROUNDING_RE = re.compile(
    r">\s*[\"“]"
    r"|--\[[^\]]*\]-->"
    r"|[\[【]\s*(?:SIM|sim_id|E\d+|EDGE|模拟|图谱|图|边)\b",
    re.I)


def audit_citation_grounding(report_markdown: str,
                             index_map: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Heuristic, offline audit (I-3-1 + REPORT-6): of the lines making a quantitative
    claim, how many are *grounded* — i.e. carry a source citation ([S1]) OR a
    simulation/edge-grounding marker (agent quote, causal-edge render, sim/edge ref)?

    ``coverage`` now counts sim/edge grounding as valid; ``source_coverage`` keeps the
    strict source-only ratio as a separate metric so a regression in real-citation
    discipline is still visible. Fast guardrail, not a semantic verifier; deterministic.

    WAVE10（无缝引用）：可选 ``index_map``（记号→来源，如 {"S12": {...}}）——传入时额外
    报告 **resolved** 指标（记号必须能在注入索引里解析才算引用，悬空的 [S246] 不再充数）：
    ``resolved_cited`` / ``resolved_coverage``。这是**独立观测指标**，发布门仍读 ``coverage``
    （本波不移动门槛，先观测一轮再收紧）。缺省 None 时输出与历史逐字节一致。
    """
    lines = [ln.strip() for ln in (report_markdown or "").splitlines() if ln.strip()]
    quant_lines = [ln for ln in lines if _NUMBER_RE.search(ln) and not ln.startswith("#")]
    if not quant_lines:
        out = {"quantitative_claims": 0, "cited": 0, "coverage": 1.0,
               "source_cited": 0, "source_coverage": 1.0, "unsupported_samples": []}
        if index_map is not None:
            out["resolved_cited"] = 0
            out["resolved_coverage"] = 1.0
        return out

    def _grounded(ln: str) -> bool:
        return bool(_CITATION_RE.search(ln) or _SIM_GROUNDING_RE.search(ln))

    grounded = [ln for ln in quant_lines if _grounded(ln)]
    source_cited = [ln for ln in quant_lines if _CITATION_RE.search(ln)]
    unsupported = [ln for ln in quant_lines if not _grounded(ln)]
    out = {
        "quantitative_claims": len(quant_lines),
        "cited": len(grounded),                                   # grounded (source or sim/edge)
        "coverage": round(len(grounded) / len(quant_lines), 3),
        "source_cited": len(source_cited),                        # source-only (separate metric)
        "source_coverage": round(len(source_cited) / len(quant_lines), 3),
        "unsupported_samples": [ln[:200] for ln in unsupported[:8]],
    }
    if index_map is not None:
        resolvable = {_norm_citation_tag(k) for k in index_map}

        def _resolves(ln: str) -> bool:
            return any(_norm_citation_tag(m.group(1)) in resolvable
                       for m in _CITATION_TAG_RE.finditer(ln))

        resolved = [ln for ln in quant_lines if _resolves(ln)]
        out["resolved_cited"] = len(resolved)
        out["resolved_coverage"] = round(len(resolved) / len(quant_lines), 3)
    return out


# WAVE10（无缝引用）：带捕获组的引用记号（位置式 S12 与遗留分层 S1-a 均接受，方/全角括号）。
_CITATION_TAG_RE = re.compile(r"[\[【]\s*(S\d+(?:-[A-Za-z])?)\s*[\]】]", re.I)


def _norm_citation_tag(tag: str) -> str:
    """把记号归一为规范键：去括号/空白、S 大写、分层后缀小写（"[ s12 ]"→"S12"）。"""
    t = re.sub(r"[\[【\]】\s]", "", str(tag or ""))
    if not t:
        return ""
    t = "S" + t[1:].lower() if t[:1] in ("s", "S") else t
    return t


def validate_citation_markers(report_markdown: str,
                              index_map: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """WAVE10（无缝引用）：正文引用记号 vs 注入索引的确定性完整性校验。

    围栏感知（``` / ~~~ 内的记号不计——代码/mermaid 块里的 [S1] 是字面内容），按首现顺序
    采集全部 [S<n>]（含遗留 [S1-a]/【S1】变体，归一后计数）。返回：
      * ``total_markers`` / ``distinct_tags`` —— 出现总数与去重数；
      * ``order`` —— 记号首现顺序（引用最终化按此给参考来源编号）；
      * ``counts`` —— {记号: 出现次数}；
      * ``dangling`` —— 在 ``index_map`` 中无法解析的记号（[S246] 型幻觉编号）；
      * ``uncited`` —— 索引里从未被正文引用的记号（观测「索引膨胀」）。
    ``index_map`` 为 None 时 dangling/uncited 恒为空（仅做记号盘点）。纯函数、无副作用。
    """
    order: List[str] = []
    counts: Dict[str, int] = {}
    in_fence = False
    for ln in (report_markdown or "").split("\n"):
        s = ln.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in _CITATION_TAG_RE.finditer(ln):
            tag = _norm_citation_tag(m.group(1))
            if not tag:
                continue
            if tag not in counts:
                order.append(tag)
            counts[tag] = counts.get(tag, 0) + 1
    dangling: List[str] = []
    uncited: List[str] = []
    if index_map is not None:
        resolvable = {_norm_citation_tag(k) for k in index_map if _norm_citation_tag(k)}
        dangling = [t for t in order if t not in resolvable]
        uncited = sorted(resolvable - set(order),
                         key=lambda t: (len(t), t))  # S2 < S10 的自然序
    return {
        "total_markers": sum(counts.values()),
        "distinct_tags": len(order),
        "order": order,
        "counts": counts,
        "dangling": dangling,
        "uncited": uncited,
    }
