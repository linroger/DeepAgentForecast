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
        cleaned.append({
            "name": str(s.get("name") or "未命名情景"),
            "probability": prob if prob is not None else 0.0,
            "summary": str(s.get("summary") or ""),
            "key_drivers": [str(x) for x in (s.get("key_drivers") or []) if x],
            "resolution_criteria": str(s.get("resolution_criteria") or ""),
        })
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
