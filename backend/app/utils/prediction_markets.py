"""Oddpool 预测市场客户端（聚合 Kalshi + Polymarket）——市场隐含概率作为预测校准锚点。

深研管线在报告/预测阶段把「与研究问题相关的真实预测市场」的隐含概率注入提示词：
市场价格是持币者的聚合信念，是极好的**校准锚点**（calibration anchor）——但不是真值。
本模块保持依赖极轻（httpx + Config），全链路 degrade-safe：无 key / 网络失败 / 解析失败
一律记一条 warning 并返回空结果，绝不向调用方抛异常、绝不阻断主流程。

API（已验证）：
    GET https://api.oddpool.com/search/markets?q=<全文检索>&status=active&limit=N
    GET https://api.oddpool.com/search/events?q=...&limit=N
    GET https://api.oddpool.com/search/events/{event_id}/markets
    鉴权头 X-API-Key；key 只从 参数 → Config.ODDPOOL_API_KEY → 环境变量 读取，绝不硬编码。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

ODDPOOL_BASE_URL = "https://api.oddpool.com"

# 可重试的瞬时错误状态码（限流/网关抖动）；4xx 鉴权/参数错误不重试。
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


def _cfg(name: str, default: Any) -> Any:
    """读取 Config 旗标（degrade-safe；Config 不可导入/属性缺失时用默认值，绝不抛异常）。"""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001 — config 导入失败不得阻断市场信号（可选增强）
        return default


def _coerce_float(v: Any) -> Optional[float]:
    """把 API 的字符串数值（如 last_yes_price="0.2100"）安全转成 float；失败返回 None。"""
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


class OddpoolClient:
    """极薄的 Oddpool REST 客户端：15s 超时、瞬时错误重试一次、任何失败降级为空结果。"""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: str = ODDPOOL_BASE_URL, timeout: float = 15.0):
        key = str(api_key or "").strip()
        if not key:
            key = str(_cfg("ODDPOOL_API_KEY", "") or "").strip()
        if not key:
            key = os.environ.get("ODDPOOL_API_KEY", "").strip()
        self.api_key = key
        self.base_url = str(base_url or ODDPOOL_BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        """有 key 且 PREDICTION_MARKETS_ENABLED（默认开）时才发请求。"""
        return bool(self.api_key) and bool(_cfg("PREDICTION_MARKETS_ENABLED", True))

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str, params: Dict[str, Any]) -> Any:
        """GET 一个端点并解析 JSON。瞬时错误（网络/超时/5xx/429）重试一次；
        其余失败记 warning 后返回 None（degrade-safe，绝不向上抛）。"""
        url = self.base_url + path
        last_err: Any = None
        for attempt in (1, 2):
            try:
                resp = httpx.get(url, params=params, timeout=self.timeout,
                                 headers={"X-API-Key": self.api_key,
                                          "Accept": "application/json"})
                if resp.status_code in _TRANSIENT_STATUS and attempt == 1:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.TransportError as e:  # 连接/超时类瞬时错误 → 重试一次
                last_err = e
                continue
            except Exception as e:  # noqa: BLE001 — 4xx/JSON 解析等非瞬时错误不重试
                last_err = e
                break
        logger.warning(f"Oddpool GET {path} 失败（降级为空结果）: {last_err}")
        return None

    # ------------------------------------------------------------- endpoints
    def search_markets(self, query: str, status: str = "active",
                       limit: int = 10) -> List[Dict[str, Any]]:
        """全文检索市场；失败/未启用返回 []。"""
        if not self.enabled or not str(query or "").strip():
            return []
        data = self._get("/search/markets",
                         {"q": str(query).strip(), "status": status, "limit": limit})
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []

    def search_events(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """全文检索事件（一个事件下挂多个市场）；失败/未启用返回 []。"""
        if not self.enabled or not str(query or "").strip():
            return []
        data = self._get("/search/events", {"q": str(query).strip(), "limit": limit})
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def event_markets(self, event_id: str) -> List[Dict[str, Any]]:
        """列出某事件下的全部市场；失败/未启用返回 []。"""
        if not self.enabled or not str(event_id or "").strip():
            return []
        data = self._get(f"/search/events/{str(event_id).strip()}/markets", {})
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []

    # -------------------------------------------------------------- snapshot
    def snapshot_for_queries(self, queries: List[str], per_query: int = 8,
                             max_total: int = 20,
                             min_volume: float = 200) -> List[Dict[str, Any]]:
        """对一组检索词取市场快照并规整化。

        规则：按 market_id 去重（首个命中的 query 记入 matched_query）；剔除已结算 /
        零流动性 / 成交量低于 min_volume / 无可解析价格的市场；按 volume 降序取前
        max_total 条。输出统一 schema：
            {market_id, exchange, question, implied_yes_prob, volume, liquidity,
             event_title, matched_query}
        Degrade-safe：任一 query 失败只丢那一批，整体绝不抛。
        """
        by_id: Dict[str, Dict[str, Any]] = {}
        for q in queries or []:
            q = str(q or "").strip()
            if not q:
                continue
            for raw in self.search_markets(q, status="active", limit=per_query):
                norm = self._normalize_market(raw, matched_query=q,
                                              min_volume=min_volume)
                if norm is None:
                    continue
                if norm["market_id"] not in by_id:
                    by_id[norm["market_id"]] = norm
        markets = sorted(by_id.values(), key=lambda m: -(m.get("volume") or 0.0))
        return markets[:max(0, int(max_total))]

    @staticmethod
    def _normalize_market(raw: Dict[str, Any], matched_query: str,
                          min_volume: float) -> Optional[Dict[str, Any]]:
        """单条市场规整化；不合格（已结算/无价/低量/零流动性）返回 None。"""
        if not isinstance(raw, dict):
            return None
        market_id = str(raw.get("market_id") or "").strip()
        question = str(raw.get("question") or "").strip()
        if not market_id or not question:
            return None
        status = str(raw.get("status") or "").strip().lower()
        if raw.get("settled_at") or status in ("settled", "closed", "finalized", "resolved"):
            return None
        prob = _coerce_float(raw.get("last_yes_price"))
        if prob is None or not (0.0 <= prob <= 1.0):
            return None  # 无可解析的隐含概率 → 作为校准锚点没有意义
        volume = _coerce_float(raw.get("volume")) or 0.0
        liquidity = _coerce_float(raw.get("liquidity")) or 0.0
        if liquidity <= 0 or volume < float(min_volume):
            return None  # 零流动性/极低量的价格是噪声，不配当锚点
        return {
            "market_id": market_id,
            "exchange": str(raw.get("exchange") or "").strip().lower(),
            "question": question,
            "implied_yes_prob": round(prob, 4),
            "volume": volume,
            "liquidity": liquidity,
            "event_title": str(raw.get("event_title") or "").strip(),
            "matched_query": matched_query,
        }


# ------------------------------------------------------------------ rendering
def _esc_cell(x: Any) -> str:
    """markdown 表格单元转义（管道符/换行），与 forecast_extractor 同风格。"""
    return str(x).replace("|", "／").replace("\n", " ").strip()


def render_markets_block(markets: List[Dict[str, Any]], lang: str = "en") -> str:
    """把市场快照渲染为确定性的 markdown 表（无 LLM；空列表 → ""，注入自动跳过）。"""
    rows = [m for m in (markets or []) if isinstance(m, dict)]
    if not rows:
        return ""
    zh = str(lang or "").lower().startswith("zh")
    title = "### Prediction Market Signals (Kalshi/Polymarket via Oddpool)"
    if zh:
        cols = "| # | 市场问题 | 交易所 | 隐含 P(yes) | 成交量 |"
        caveat = ("_以上为机器抓取的活跃市场快照，价格随时变动；市场隐含概率是校准锚点，"
                  "不是真值——引用前注意时效。_")
    else:
        cols = "| # | Market question | Venue | Implied P(yes) | Volume |"
        caveat = ("_Machine-fetched snapshot of active markets; prices move continuously. "
                  "Market-implied probabilities are calibration anchors, not ground truth — "
                  "mind freshness before relying on them._")
    lines = [title, "", cols, "|---|---|---|---|---|"]
    for i, m in enumerate(rows, 1):
        prob = _coerce_float(m.get("implied_yes_prob"))
        pct = f"{prob * 100:.0f}%" if prob is not None else "—"
        vol = _coerce_float(m.get("volume"))
        vol_s = f"{vol:,.0f}" if vol is not None else "—"
        lines.append("| {i} | {q} ({mid}) | {ex} | {p} | {v} |".format(
            i=i,
            q=_esc_cell(str(m.get("question") or "")[:160]),
            mid=_esc_cell(m.get("market_id") or ""),
            ex=_esc_cell(m.get("exchange") or "—"),
            p=pct,
            v=vol_s,
        ))
    lines += ["", caveat]
    return "\n".join(lines)


# ------------------------------------------------------------ query derivation
# 全文检索用「短词组」效果最好（如 'tariff' / 'semiconductor export' / 'AI capex'）；
# 从研究问题启发式抽取显著名词短语（停用词过滤，无需 LLM），再并入 hot_topics 与
# 高显著度 actor 名，去重后限量。确定性：同输入必得同输出。
_QUERY_STOPWORDS = frozenset("""
a an the and or but of to in on for with by from at as is are was were be been being
will would could should shall may might must can do does did not no nor this that these
those it its their his her our your my we they you he she i who whom whose which what
when where why how whether if than then so such very just now here there per via about
into over under between among during before after above below up down out off again
further once more most other some any all both each few own same too s t don also
year years month months week weeks day days future likely impact effect effects report
research question forecast prediction predict analysis analyze scenario scenarios
""".split())

# 英文/数字词 或 连续 CJK 串（CJK 不按空格分词，整段作为一个候选 token）。
_QUERY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|[一-鿿]{2,}")


def _salient_phrases(text: str, max_words: int = 3) -> List[str]:
    """从文本抽取显著短语：连续的非停用词 token 聚成 ≤max_words 词的短语（确定性启发式）。"""
    phrases: List[str] = []
    cur: List[str] = []

    def _flush() -> None:
        if cur:
            phrases.append(" ".join(cur))
            cur.clear()

    for tok in _QUERY_TOKEN_RE.findall(str(text or "")):
        is_cjk = bool(re.match(r"[一-鿿]", tok))
        if is_cjk:
            cur.append(tok[:12])  # CJK 串过长时截断（全文检索短词更有效）
            _flush()
            continue
        # 全大写缩略词（AI/EU/GDP）即使短也保留；其余 <3 字符的英文词按噪声丢弃。
        if tok.lower() in _QUERY_STOPWORDS or (len(tok) < 3 and not tok.isupper()):
            _flush()
            continue
        cur.append(tok)
        if len(cur) >= max_words:
            _flush()
    _flush()
    # 去重保序
    seen: set = set()
    out: List[str] = []
    for p in phrases:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def derive_market_queries(question: str, hot_topics: Optional[List[str]] = None,
                          actor_names: Optional[List[str]] = None,
                          max_queries: int = 6, max_words: int = 5) -> List[str]:
    """确定性派生市场检索词：研究问题关键短语 + hot_topics + 高显著度 actor 名。

    每条 ≤max_words 词（全文检索短词效果最好），去重限量到 max_queries。纯启发式、
    无 LLM 调用；空输入返回 []。
    """
    out: List[str] = []
    seen: set = set()

    def _add(q: Any) -> None:
        s = re.sub(r"\s+", " ", str(q or "")).strip(" \t\"'.,;:!?()[]{}")
        if not s:
            return
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words])
        k = s.lower()
        if k in seen or len(out) >= max_queries:
            return
        seen.add(k)
        out.append(s)

    for ph in _salient_phrases(question, max_words=3)[:3]:
        _add(ph)
    for t in (hot_topics or [])[:4]:
        _add(t)
    for n in (actor_names or [])[:3]:
        _add(n)
    return out
