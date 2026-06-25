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

import re
from typing import Any, Dict, List, Optional

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
            s["probability"] = round(s["probability"] / total, 4)
    return cleaned


def extract_structured_forecast(report_markdown: str, llm,
                                situation_brief: Optional[str] = None) -> Dict[str, Any]:
    """Run one LLM pass to produce a validated structured forecast object.

    ``llm`` must expose ``chat_json(messages, temperature, max_tokens)``. Returns a
    dict with normalized scenarios; raises nothing on a malformed model reply
    beyond what chat_json raises (caller wraps in try/except and degrades).
    """
    content = report_markdown or ""
    if len(content) > 40000:
        content = content[:40000] + "\n…(truncated)…"
    user = _FORECAST_INSTRUCTIONS
    if situation_brief:
        user += f"\n\n[态势简报]\n{situation_brief[:2000]}"
    user += f"\n\n[预测报告]\n{content}"
    raw = llm.chat_json(
        messages=[{"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=2048,
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
    return {
        "headline": str(raw.get("headline") or ""),
        "horizon": str(raw.get("horizon") or ""),
        "scenarios": scenarios,
        "key_uncertainties": [str(x) for x in (raw.get("key_uncertainties") or []) if x],
        "confidence": confidence,
        "confidence_rationale": str(raw.get("confidence_rationale") or ""),
        "schema_version": 1,
    }


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


def derive_forecast_spine(llm, *, central_question: str = "", horizon: str = "",
                          situation_brief: Optional[str] = None,
                          forecast_inputs: str = "", signal_pack: str = "") -> Dict[str, Any]:
    """NEXTSTEPS P0-1: derive the structured forecast *spine* from research +
    simulation SIGNALS — *before* any prose is written.

    This forces MECE probabilistic discipline up front and yields a spine whose
    scenarios+probabilities+resolution_criteria each later report section must
    defend, rather than reverse-engineering numbers out of finished narrative.
    Same output shape as ``extract_structured_forecast`` plus ``derived_from='spine'``.
    Degrade-safe: a malformed reply yields a well-formed empty forecast; the caller
    wraps in try/except and falls back to the post-hoc extractor.
    """
    user = _SPINE_INSTRUCTIONS
    if central_question:
        user += f"\n\n[核心问题]\n{central_question[:600]}"
    if horizon:
        user += f"\n\n[预测时间范围]\n{horizon[:120]}"
    if situation_brief:
        user += f"\n\n[态势简报]\n{situation_brief[:2000]}"
    if forecast_inputs:
        user += f"\n\n[研究输入：参考类基率 / 驱动因素 / 观察指标 / 候选情景]\n{forecast_inputs[:4000]}"
    if signal_pack:
        user += f"\n\n[模拟量化信号]\n{signal_pack[:4000]}"
    raw = llm.chat_json(
        messages=[{"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=2048,
    )
    if not isinstance(raw, dict):
        raw = {}
    out = _assemble_forecast(raw)
    out["derived_from"] = "spine"
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


def render_resolution_block(forecast: Optional[Dict[str, Any]],
                            indicators: Optional[List[Dict[str, Any]]] = None) -> str:
    """NEXTSTEPS P2-2: 渲染一个**确定性**的「如何验证本预测」章节。

    逐情景列出可证伪的判定标准 + 来自 forecast_inputs 的带日期/触发型观察指标（并把指标绑定到
    它所判别的情景）。一个没有明确、可观测、带日期指标的预测无法被追踪或打分——这正是"利率可能
    上升" vs "若指标 X 于日期 Z 前超过 Y，则情景 A 确认"的区别。forecast 无情景 → ""（不追加）。
    """
    if not isinstance(forecast, dict):
        return ""
    scenarios = forecast.get("scenarios") or []
    if not scenarios:
        return ""
    lines = [
        "## 如何验证本预测（判定标准与观察指标）",
        "本节给出每个情景**可证伪、可追踪**的判定标准与到期/触发型观察指标，供日后核对与校准。",
        "",
        "### 各情景判定标准",
    ]
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        try:
            pct = f"{float(s.get('probability') or 0.0) * 100:.0f}%"
        except (TypeError, ValueError):
            pct = "—"
        name = str(s.get("name") or "未命名情景")
        crit = str(s.get("resolution_criteria") or "").strip() or "（缺明确判定标准——需补全）"
        lines.append(f"- **[{pct}] {name}**：{crit}")
    inds = [i for i in (indicators or []) if isinstance(i, dict)]
    if inds:
        lines.append("")
        lines.append("### 观察指标（到期/触发即核对）")
        lines.append("| 指标 | 到期/触发 | 关联情景 |")
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
        out["critiqued"] = True
        return out
    except Exception:
        return forecast


# ---------------------------------------------------------------- citation audit
# A "quantitative claim" = a sentence/line carrying a number or percentage. We
# check whether each such line is near a citation marker ([S1], 【S3】, etc.).
_CITATION_RE = re.compile(r"[\[【]\s*S\d+\s*[\]】]", re.I)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*%|\b\d{2,}(?:\.\d+)?\b|\b\d{4}年")


def audit_citation_grounding(report_markdown: str) -> Dict[str, Any]:
    """Heuristic, offline audit (I-3-1): of the lines making a quantitative claim,
    how many carry a citation marker? Returns coverage ratio + unsupported samples.

    This is a fast guardrail, not a semantic verifier — it surfaces ungrounded
    numbers for review rather than proving each claim. Pure / deterministic.
    """
    lines = [ln.strip() for ln in (report_markdown or "").splitlines() if ln.strip()]
    quant_lines = [ln for ln in lines if _NUMBER_RE.search(ln) and not ln.startswith("#")]
    if not quant_lines:
        return {"quantitative_claims": 0, "cited": 0, "coverage": 1.0, "unsupported_samples": []}
    cited = [ln for ln in quant_lines if _CITATION_RE.search(ln)]
    unsupported = [ln for ln in quant_lines if not _CITATION_RE.search(ln)]
    return {
        "quantitative_claims": len(quant_lines),
        "cited": len(cited),
        "coverage": round(len(cited) / len(quant_lines), 3),
        "unsupported_samples": [ln[:200] for ln in unsupported[:8]],
    }
