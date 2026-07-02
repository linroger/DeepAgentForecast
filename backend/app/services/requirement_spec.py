"""QUALITY-OPT F0 — parse the forecast brief into a machine-readable output contract.

The brief (saved verbatim as ``prediction_requirement.txt``) carries the deliverable's
format requirements: what language to write in, how many binary forecasts, a part/section
structure, a page budget. Historically only the research stage read it; the report/forecast
finalizer ran a generic hardcoded contract (5 scenarios, Chinese, 5-8 free chapters). This
parser turns the brief into a small spec the downstream stages can honor, so instruction-
following stops depending on luck.

Dependency-light + side-effect-free (pure functions on the brief text) → unit-testable.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Explicit-language overrides the author may write into the brief.
_LANG_DIRECTIVES = [
    (re.compile(r"write (?:the )?(?:report|submission|forecast|response|answer) in english", re.I), "English"),
    (re.compile(r"\b(?:in|用)\s*english\b", re.I), "English"),
    (re.compile(r"用中文(?:撰写|回答|写)?|以中文(?:撰写|回答)?|report in chinese", re.I), "Chinese"),
]


def _cjk_ratio(text: str) -> float:
    """Fraction of CJK characters among all letter-ish characters (cheap language sniff)."""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    letters = sum(1 for ch in text if ch.isalpha() or "一" <= ch <= "鿿")
    return (cjk / letters) if letters else 0.0


def detect_output_language(*texts: Optional[str]) -> str:
    """Decide the report's output language.

    Order: an explicit in-brief directive wins; else sniff the dominant script of the
    supplied texts (brief + research report). Default English (forecast briefs are
    overwhelmingly English and a wrong-language submission is an automatic round-one fail).
    """
    joined = "\n".join(t for t in texts if t)
    for rx, lang in _LANG_DIRECTIVES:
        if rx.search(joined):
            return lang
    return "Chinese" if _cjk_ratio(joined) >= 0.10 else "English"


def _parse_binary_min(text: str) -> Optional[int]:
    """Find a requested minimum count of binary/yes-no forecasts ('10+ binary forecasts')."""
    if not text:
        return None
    for rx in (
        re.compile(r"(\d{1,2})\s*\+?\s*binary", re.I),
        re.compile(r"minimum of\s*(\d{1,2})\s*binary", re.I),
        re.compile(r"at least\s*(\d{1,2})\s*(?:binary|yes/no|forecasts)", re.I),
        re.compile(r"(\d{1,2})\s*\+?\s*(?:yes/no|yes-no)\s*forecasts", re.I),
    ):
        m = rx.search(text)
        if m:
            try:
                return max(1, int(m.group(1)))
            except ValueError:
                pass
    return None


def _parse_page_budget(text: str) -> Optional[int]:
    """Find a total page ceiling ('10 pages or less', 'aim for 10 pages')."""
    if not text:
        return None
    for rx in (
        re.compile(r"(\d{1,3})\s*pages?\s*or\s*less", re.I),
        re.compile(r"(?:aim for|under|max(?:imum)?(?: of)?|<=?)\s*(\d{1,3})\s*pages?", re.I),
        re.compile(r"(\d{1,3})\s*pages?\s*(?:total|maximum|max)", re.I),
    ):
        m = rx.search(text)
        if m:
            try:
                return max(1, int(m.group(1)))
            except ValueError:
                pass
    return None


def parse_requirement_spec(brief_text: Optional[str],
                           research_report: Optional[str] = None) -> Dict[str, Any]:
    """Return the machine contract: {output_language, binary_min_count, page_budget,
    wants_binary, themes}. All fields degrade to safe defaults when the brief is silent."""
    text = brief_text or ""
    low = text.lower()
    binary_min = _parse_binary_min(text)
    themes = []
    if re.search(r"mercantilis", low):
        themes.append("mercantilism")
    if re.search(r"\bai\b|artificial intelligence", low):
        themes.append("ai")
    if "intersection" in low or ("collid" in low) or ("intersect" in low):
        themes.append("intersection")
    return {
        "output_language": detect_output_language(text, research_report),
        "wants_binary": bool(binary_min) or bool(re.search(r"binary|yes/no|yes-no", low)),
        "binary_min_count": binary_min,
        "page_budget": _parse_page_budget(text),
        "themes": themes,
    }
