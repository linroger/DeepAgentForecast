"""DRF-2 config-reflected tool: Polymarket 预测市场检索（官方公开 Gamma API，keyless）。

REDESIGN.md 能力映射：`prediction_markets` → config-reflected **tool** + thin skill。
本模块是 deer-flow 2.0 harness 的 `tools:` 条目入口（config.yaml 里
`use: drf2.config.market_tools:prediction_market_search_tool`），由 harness 进程经
reflection (`resolve_variable`) 加载，因此：

* **自包含**：不 import `backend/app`（那会拉起 Flask/Config 全家桶）；逻辑与
  `backend/app/utils/prediction_markets.py` 的已验证行为保持一致（同样的端点、
  过滤规则、degrade-safe 语义）。数据源是 Polymarket 官方公开 Gamma API——检索/
  浏览公开市场无需 API key、无需钱包，因此本工具**无需任何凭证**。
* **degrade-safe**：网络失败 / 解析失败一律返回带说明的空结果字符串，绝不向 harness
  抛异常、绝不阻断主流程。
* **langchain 可选**：harness 环境有 langchain_core，模块底部把纯函数包成 BaseTool；
  在没有 langchain 的环境（如 legacy backend venv 里跑离线单测）import 本模块仍成功，
  纯逻辑函数可直接测试。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"

# 可重试的瞬时错误状态码（限流/网关抖动）；4xx 参数错误不重试。
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

# 过滤阈值（与 legacy prediction_markets.snapshot_for_queries 缺省一致）
DEFAULT_MIN_VOLUME = 200.0
DEFAULT_PER_QUERY = 8
DEFAULT_MAX_TOTAL = 20
DEFAULT_MAX_PER_EVENT = 3
DEFAULT_TIMEOUT = 15.0


def _coerce_float(v: Any) -> Optional[float]:
    """把 API 的字符串数值（如 volume="32970.32"）安全转成 float；失败返回 None。"""
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _as_list(v: Any) -> List[Any]:
    """Polymarket 的 outcomes/outcomePrices 常是 JSON 串（'["Yes","No"]'）也可能已是 list。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _yes_price(outcomes: Any, prices: Any) -> Optional[float]:
    """从 outcomes/outcomePrices 取 "Yes" 对应的隐含概率；无法定位则 None。"""
    names = _as_list(outcomes)
    px = _as_list(prices)
    if not names or not px or len(names) != len(px):
        return None
    for i, name in enumerate(names):
        if str(name).strip().lower() == "yes":
            return _coerce_float(px[i])
    return None


def _http_get(path: str, params: Dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET 一个 Polymarket 端点并解析 JSON（keyless）。瞬时错误重试一次；任何失败返回 None（绝不上抛）。"""
    try:
        import httpx
    except ImportError:  # noqa: BLE001 — 环境无 httpx → 空结果，不阻断
        logger.warning("prediction_market_search: httpx 不可用，降级为空结果")
        return None
    url = POLYMARKET_BASE_URL + path
    last_err: Any = None
    for attempt in (1, 2):
        try:
            resp = httpx.get(url, params=params, timeout=timeout,
                             headers={"Accept": "application/json"})
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
    logger.warning(f"Polymarket GET {path} 失败（降级为空结果）: {last_err}")
    return None


def normalize_market(raw: Any, matched_query: str,
                     min_volume: float = DEFAULT_MIN_VOLUME,
                     event_title: str = "") -> Optional[Dict[str, Any]]:
    """单条 Polymarket 市场规整化；不合格（已关闭/无价/定盘价/低量）返回 None。

    规则与 legacy PolymarketClient._normalize_market 逐条一致——市场只有在未关闭、
    价格严格落在 (0,1)、volume>=min_volume 时才配当校准锚点。
    """
    if not isinstance(raw, dict):
        return None
    market_id = str(raw.get("id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not market_id or not question:
        return None
    # 已关闭/已判定 → 不配当锚点（active 旗标在已判定市场上仍为 True，不可靠）。
    if raw.get("closed") is True or str(raw.get("closed")).strip().lower() == "true":
        return None
    prob = _yes_price(raw.get("outcomes"), raw.get("outcomePrices"))
    # 价格须严格落在 (0,1)：恰为 0/1 = 市场实质已定盘，作为校准锚点没有意义。
    if prob is None or not (0.0 < prob < 1.0):
        return None
    volume = _coerce_float(raw.get("volume")) or 0.0
    liquidity = _coerce_float(raw.get("liquidity")) or 0.0
    if volume < float(min_volume):
        return None  # 极低量的价格是噪声，不配当锚点
    return {
        "market_id": market_id,
        "exchange": "polymarket",
        "question": question,
        "implied_yes_prob": round(prob, 4),
        "volume": volume,
        "liquidity": liquidity,
        "event_title": event_title or str(raw.get("groupItemTitle") or "").strip(),
        "matched_query": matched_query,
    }


def _cap_per_event(ranked: List[Dict[str, Any]], max_per_event: int,
                   max_total: int) -> List[Dict[str, Any]]:
    """在已按 volume 降序的市场列表上，限制每个事件最多 max_per_event 条，再截到 max_total。
    保证多事件多样性（一个多结局事件的子市场阶梯不霸占全部名额）。<=0 视为不限制。"""
    if int(max_per_event) <= 0:
        return ranked[:max(0, int(max_total))]
    per_event: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for m in ranked:
        key = str(m.get("event_title") or "").strip() or m.get("market_id") or ""
        n = per_event.get(key, 0)
        if n >= int(max_per_event):
            continue
        per_event[key] = n + 1
        out.append(m)
    return out[:max(0, int(max_total))]


def snapshot_for_queries(queries: List[str],
                         per_query: int = DEFAULT_PER_QUERY,
                         max_total: int = DEFAULT_MAX_TOTAL,
                         min_volume: float = DEFAULT_MIN_VOLUME,
                         max_per_event: int = DEFAULT_MAX_PER_EVENT,
                         fetch: Any = None) -> List[Dict[str, Any]]:
    """对一组检索词取活跃市场快照并规整化（按 market_id 去重，volume 降序，每事件≤max_per_event，限量）。

    ``fetch(query, limit) -> list[event]`` 可注入（单测 mock 网络）；缺省走 Polymarket
    /public-search（每个事件下挂多个市场，展开后规整）。Degrade-safe：任一 query 失败
    只丢那一批，整体绝不抛。
    """
    def _default_fetch(q: str, limit: int) -> List[Any]:
        data = _http_get("/public-search", {"q": q, "limit_per_type": limit,
                                            "events_status": "active"})
        events = data.get("events") if isinstance(data, dict) else None
        return events if isinstance(events, list) else []

    fetch_fn = fetch if fetch is not None else _default_fetch

    by_id: Dict[str, Dict[str, Any]] = {}
    for q in queries or []:
        q = str(q or "").strip()
        if not q:
            continue
        try:
            events = fetch_fn(q, per_query)
        except Exception as e:  # noqa: BLE001 — 单个 query 失败不阻断整体
            logger.warning(f"prediction_market_search: query {q!r} 失败（跳过）: {e}")
            continue
        for event in events or []:
            if not isinstance(event, dict):
                continue
            event_title = str(event.get("title") or "").strip()
            for raw in event.get("markets") or []:
                norm = normalize_market(raw, matched_query=q, min_volume=min_volume,
                                        event_title=event_title)
                if norm is None:
                    continue
                if norm["market_id"] not in by_id:
                    by_id[norm["market_id"]] = norm
    ranked = sorted(by_id.values(), key=lambda m: -(m.get("volume") or 0.0))
    return _cap_per_event(ranked, max_per_event, max_total)


def prediction_market_search_impl(queries: str) -> str:
    """纯逻辑入口：换行/逗号分隔的短检索词 → 市场快照 JSON 文本（LLM 可读）。"""
    parts = [p.strip() for chunk in str(queries or "").splitlines()
             for p in chunk.split(",")]
    qlist = [p for p in parts if p][:6]
    if not qlist:
        return json.dumps({"markets": [], "note": "no queries given"}, ensure_ascii=False)
    markets = snapshot_for_queries(qlist)
    note = ("Machine-fetched snapshot of active Polymarket markets (public Gamma API, no key); "
            "prices move continuously. Market-implied probabilities are calibration "
            "anchors, not ground truth.")
    if not markets:
        note = "No active, liquid markets matched these queries. " + note
    return json.dumps({"queries": qlist, "markets": markets, "note": note},
                      ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# harness 入口：langchain BaseTool（config.yaml `use:` 指向这个变量）。
# legacy backend venv 无 langchain —— import 本模块仍需成功以便离线单测纯逻辑，
# 故 langchain 缺失时该变量为 None（harness 环境必有 langchain，不受影响）。
# ---------------------------------------------------------------------------
try:
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool("prediction_market_search")
    def prediction_market_search_tool(queries: str) -> str:
        """Search active prediction markets (Polymarket, public API — no key) for calibration anchors.

        Args:
            queries: Up to 6 SHORT search phrases (max ~5 words each), separated by
                newlines or commas — e.g. "tariff, semiconductor export, Fed rate cut".
                Full-text search works best with short salient phrases, not sentences.

        Returns:
            JSON with the filtered market snapshot: for each market its market_id,
            exchange, question, implied_yes_prob (last yes price), volume, liquidity,
            event_title and matched_query. Closed, unpriced, resolved (price 0/1), or
            low-volume markets are excluded. Empty markets list means no liquid overlap —
            that is an answer, never fabricate market prices.
        """
        return prediction_market_search_impl(queries)

except ImportError:  # noqa: BLE001 — 离线/legacy 环境无 langchain：纯逻辑仍可测
    prediction_market_search_tool = None  # type: ignore[assignment]
