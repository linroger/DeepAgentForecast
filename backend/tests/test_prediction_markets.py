"""预测市场集成（Polymarket 公开 Gamma API，keyless）的离线测试。

全部 mock httpx，绝不发真实请求：客户端解析/规整化/去重/降级、markdown 渲染、
检索词派生启发式、以及 forecast_extractor 的 market_anchor 贯通。
"""

import json

import httpx
import pytest

from app.utils import prediction_markets as pm
from app.utils.prediction_markets import (
    PolymarketClient,
    derive_market_queries,
    render_markets_block,
)
from app.services.forecast_extractor import (
    derive_forecast_spine,
    extract_binary_forecasts,
    render_binary_forecasts_block,
)
from tests.conftest import FakeLLMClient


# ---------------------------------------------------------------- helpers

class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None)

    def json(self):
        return self._payload


def _raw(mid, vol=1000, liq=50, yes="0.2100", closed=False,
         outcomes='["Yes","No"]', prices=None, **kw):
    """一条 Polymarket /public-search 里嵌套的 market 行（字符串价，镜像真实 API）。"""
    if prices is None:
        prices = json.dumps([yes, f"{1 - float(yes):.4f}"])
    d = {
        "id": mid, "question": f"Will {mid} resolve yes?",
        "outcomes": outcomes, "outcomePrices": prices,
        "volume": vol, "liquidity": liq, "closed": closed, "active": True,
        "groupItemTitle": "",
    }
    d.update(kw)
    return d


def _event(title, markets):
    return {"title": title, "slug": "s", "markets": markets}


def _payload(events):
    return {"events": events, "pagination": {}}


@pytest.fixture
def enabled(monkeypatch):
    """打开 PREDICTION_MARKETS_ENABLED（覆盖 conftest 的 autouse 关闭），使 client.enabled=True。"""
    monkeypatch.setenv("PREDICTION_MARKETS_ENABLED", "true")
    from app.config import Config
    monkeypatch.setattr(Config, "PREDICTION_MARKETS_ENABLED", True, raising=False)


# ---------------------------------------------------------------- client

def test_disabled_makes_no_http_call(monkeypatch):
    """默认（conftest）关闭时：client.enabled=False，绝不发请求。"""
    calls = []
    monkeypatch.setattr(pm.httpx, "get", lambda *a, **k: calls.append(1))
    client = PolymarketClient()
    assert client.enabled is False
    assert client.search_events("tariff") == []
    assert client.snapshot_for_queries(["tariff"]) == []
    assert calls == []  # 未启用时绝不发请求


def test_search_events_parses_and_keyless(enabled, monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse(_payload([_event("Ev", [_raw("m1")]), "not-a-dict"]))

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    out = PolymarketClient().search_events("tariff", limit=5)
    assert [e["title"] for e in out] == ["Ev"]           # 非 dict 事件被丢弃
    assert seen["url"].endswith("/public-search")
    assert seen["params"] == {"q": "tariff", "limit_per_type": 5,
                              "events_status": "active"}
    assert "X-API-Key" not in seen["headers"]            # keyless：不发鉴权头
    assert seen["timeout"] == 15.0


def test_transport_error_retries_once_then_degrades_to_empty(enabled, monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    assert PolymarketClient().search_events("tariff") == []
    assert len(calls) == 2  # 瞬时错误恰好重试一次，然后降级为空（绝不抛）


def test_transient_5xx_retries_then_succeeds(enabled, monkeypatch):
    responses = [FakeResponse(status_code=503),
                 FakeResponse(_payload([_event("Ev", [_raw("m1")])]))]
    monkeypatch.setattr(pm.httpx, "get", lambda *a, **k: responses.pop(0))
    out = PolymarketClient().search_events("tariff")
    assert [e["title"] for e in out] == ["Ev"]


def test_non_transient_4xx_does_not_retry(enabled, monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return FakeResponse(status_code=404)

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    assert PolymarketClient().search_events("tariff") == []
    assert len(calls) == 1  # 4xx 不重试


# ------------------------------------------------------------- snapshot

def test_snapshot_normalizes_filters_dedupes_and_sorts(enabled, monkeypatch):
    by_query = {
        "tariff": _payload([
            _event("Big", [_raw("m-big", vol=9000, yes="0.6400")]),
            _event("Bad", [
                _raw("m-closed", vol=8000, closed=True),                    # 已关闭 → 剔除
                _raw("m-resolved", vol=8000, prices='["1","0"]'),           # 价=1 定盘 → 剔除
                _raw("m-thin", vol=50),                                     # 低于 min_volume → 剔除
                _raw("m-noprice", vol=5000, prices='["","x"]'),            # 无可解析价 → 剔除
            ]),
        ]),
        "semiconductor export": _payload([
            _event("Big", [_raw("m-big", vol=9000, yes="0.6400")]),         # 跨 query 重复 → 去重
            _event("Small", [_raw("m-small", vol=500, yes="0.2100")]),
        ]),
    }

    def fake_get(url, params=None, timeout=None, headers=None):
        return FakeResponse(by_query[params["q"]])

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    out = PolymarketClient().snapshot_for_queries(
        ["tariff", "semiconductor export"], min_volume=200)
    assert [m["market_id"] for m in out] == ["m-big", "m-small"]  # volume 降序
    big = out[0]
    assert big["implied_yes_prob"] == 0.64          # "0.6400" 字符串 → float
    assert big["matched_query"] == "tariff"          # 首个命中的 query
    assert big["exchange"] == "polymarket"
    assert big["event_title"] == "Big"               # 事件标题回填
    assert out[1]["matched_query"] == "semiconductor export"


def test_snapshot_max_total_cap_and_query_failure_isolation(enabled, monkeypatch):
    def fake_get(url, params=None, timeout=None, headers=None):
        if params["q"] == "bad":
            raise httpx.ConnectError("down")
        return FakeResponse(_payload([
            _event("E", [_raw(f"m{i}-{params['q']}", vol=1000 + i) for i in range(5)])]))

    monkeypatch.setattr(pm.httpx, "get", fake_get)
    out = PolymarketClient().snapshot_for_queries(["a", "bad", "b"], max_total=3)
    assert len(out) == 3                            # 限量
    assert all("bad" not in m["market_id"] for m in out)  # 失败 query 只丢那一批


def test_snapshot_caps_markets_per_event_for_diversity(enabled, monkeypatch):
    """一个多结局事件的高成交量子市场阶梯不得霸占全部名额——每事件默认最多 3 条，
    其余名额留给其他事件（用户诉求：surface 多个不同事件的市场，而非一个事件的阶梯）。"""
    ladder = [_raw(f"cut{i}", vol=9000 - i, yes="0.01") for i in range(8)]   # 同一事件 8 个子市场
    payload = _payload([
        _event("Fed rate cuts count", ladder),
        _event("House control", [_raw("house", vol=100, yes="0.55")]),        # 另一事件（低量）
    ])
    monkeypatch.setattr(pm.httpx, "get", lambda *a, **k: FakeResponse(payload))
    out = PolymarketClient().snapshot_for_queries(["x"], min_volume=50, max_per_event=3)
    fed = [m for m in out if m["event_title"] == "Fed rate cuts count"]
    assert len(fed) == 3                                    # 阶梯被限到 3 条
    assert any(m["event_title"] == "House control" for m in out)  # 其他事件仍能露出
    # max_per_event<=0 → 不限制
    unlimited = PolymarketClient().snapshot_for_queries(["x"], min_volume=50, max_per_event=0)
    assert len([m for m in unlimited if m["event_title"] == "Fed rate cuts count"]) == 8


def test_snapshot_yes_price_by_outcome_index(enabled, monkeypatch):
    """outcomes 顺序颠倒时，仍按 "Yes" 的下标取价，而非盲取第一个。"""
    payload = _payload([_event("E", [
        _raw("m-rev", vol=3000, outcomes='["No","Yes"]', prices='["0.30","0.70"]')])])
    monkeypatch.setattr(pm.httpx, "get", lambda *a, **k: FakeResponse(payload))
    out = PolymarketClient().snapshot_for_queries(["x"])
    assert out[0]["implied_yes_prob"] == 0.70       # 取 "Yes"（下标 1）而非 0.30


# ------------------------------------------------------------- rendering

def test_render_markets_block_deterministic_table():
    markets = [
        {"market_id": "m1", "exchange": "polymarket", "question": "Will tariffs exceed 10%?",
         "implied_yes_prob": 0.64, "volume": 9000, "liquidity": 50,
         "event_title": "Tariffs", "matched_query": "tariff"},
        {"market_id": "m2", "exchange": "polymarket", "question": "Fed cuts | twice?",
         "implied_yes_prob": 0.21, "volume": 500, "liquidity": 10,
         "event_title": "Fed", "matched_query": "Fed rate"},
    ]
    block = render_markets_block(markets, lang="en")
    assert "Prediction Market Signals (Polymarket)" in block
    assert "| 1 | Will tariffs exceed 10%? (m1) | polymarket | 64% | 9,000 |" in block
    assert "Fed cuts ／ twice?" in block             # 管道符转义
    assert "calibration anchors, not ground truth" in block  # 时效/非真值告示
    # 中文渲染 + 空列表降级
    zh = render_markets_block(markets, lang="zh")
    assert "隐含 P(yes)" in zh and "校准锚点" in zh
    assert render_markets_block([]) == ""
    assert render_markets_block(None) == ""


# ------------------------------------------------------- query derivation

def test_derive_market_queries_heuristic():
    q = derive_market_queries(
        "Will the US semiconductor export controls tighten before 2027?",
        hot_topics=["tariff escalation", "AI capex boom cycle expansion and more"],
        actor_names=["Federal Reserve", "NVIDIA"],
    )
    assert q  # 非空
    assert all(len(x.split()) <= 5 for x in q)       # 每条 ≤5 词
    assert len(q) <= 6                               # 限量
    assert len({x.lower() for x in q}) == len(q)     # 去重
    joined = " || ".join(q)
    assert "semiconductor" in joined                 # 问题关键短语被抽出
    assert "the" not in [w.lower() for x in q for w in x.split()]  # 停用词被滤掉
    assert "tariff escalation" in q                  # hot_topics 直通
    assert "AI capex boom cycle expansion" in q      # 超长 topic 截到 5 词
    assert "Federal Reserve" in q and "NVIDIA" in q  # actor 名直通


def test_derive_market_queries_dedupes_and_handles_empty():
    assert derive_market_queries("") == []
    q = derive_market_queries("tariff tariff tariff", hot_topics=["Tariff"])
    assert [x.lower() for x in q].count("tariff") == 1  # 大小写不敏感去重


def test_derive_market_queries_cjk_question():
    q = derive_market_queries("美联储会在2026年降息吗？关税战是否升级？")
    assert q  # CJK 串作为整体 token 被抽出
    assert any("关税" in x or "美联储" in x for x in q)


# --------------------------------------------- forecast_extractor plumbing

_MARKETS = [
    {"market_id": "mkt-1", "exchange": "polymarket", "question": "Tariffs > 10%?",
     "implied_yes_prob": 0.30, "volume": 9000, "liquidity": 50,
     "event_title": "Tariffs", "matched_query": "tariff"},
]
_MARKET_PACK = render_markets_block(_MARKETS, lang="en")


def test_binary_forecasts_market_anchor_plumbing(enabled):
    fake = FakeLLMClient(json_responses=[{
        "binary_forecasts": [
            {"id": "F1", "statement": "US effective tariff rate averages over 10% from 2026-2028",
             "probability": 0.25, "resolution_criteria": "USITC tariff data > 10% by 2028",
             "theme": "trade", "horizon_year": 2028,
             "market_anchor": {"market_id": "mkt-1", "implied_yes_prob": 0.99}},
            {"id": "F2", "statement": "AI capex exceeds $500B in 2027",
             "probability": 0.80, "resolution_criteria": "Aggregate hyperscaler capex > $500B in 2027",
             "theme": "ai", "horizon_year": 2027,
             "market_anchor": {"implied_yes_prob": 0.5}},   # 缺 market_id → 锚点被丢弃
        ],
    }])
    out = extract_binary_forecasts(
        "dossier text", fake, min_count=2, language="English",
        market_pack=_MARKET_PACK, markets=_MARKETS)
    binaries = out["binary_forecasts"]
    assert len(binaries) == 2
    f1 = next(b for b in binaries if "tariff" in b["statement"])
    # 隐含概率以我们的快照价（0.30）为准，不盲信模型转录的 0.99；divergence 确定性计算。
    assert f1["market_anchor"] == {"market_id": "mkt-1", "implied_yes_prob": 0.30,
                                   "divergence": round(0.25 - 0.30, 4)}
    f2 = next(b for b in binaries if "capex" in b["statement"])
    assert "market_anchor" not in f2
    # 提示词包含市场校准指令 + 市场表
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "MARKET CALIBRATION" in prompt
    assert "[Prediction market signals]" in prompt
    assert "mkt-1" in prompt


def test_binary_forecasts_without_market_pack_prompt_unchanged():
    fake = FakeLLMClient(json_responses=[{
        "binary_forecasts": [
            {"id": "F1", "statement": "S1", "probability": 0.25,
             "resolution_criteria": "metric > 1 by 2027", "theme": "t"},
            {"id": "F2", "statement": "S2", "probability": 0.80,
             "resolution_criteria": "metric > 2 by 2027", "theme": "t"},
        ],
    }])
    out = extract_binary_forecasts("dossier", fake, min_count=2, language="English")
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "MARKET CALIBRATION" not in prompt
    assert "[Prediction market signals]" not in prompt
    assert all("market_anchor" not in b for b in out["binary_forecasts"])


def test_render_binary_block_market_column_only_when_anchored():
    anchored = {
        "binary_forecasts": [
            {"id": "F1", "statement": "Tariffs stay above 10%", "probability": 0.25,
             "resolution_criteria": "USITC > 10% by 2028", "theme": "trade",
             "market_anchor": {"market_id": "mkt-1", "implied_yes_prob": 0.30,
                               "divergence": -0.05}},
            {"id": "F2", "statement": "AI capex exceeds $500B", "probability": 0.80,
             "resolution_criteria": "capex > $500B in 2027", "theme": "ai"},
        ],
        "binary_quality": {"count": 2, "conviction_count": 2, "sharp_criteria_count": 2},
    }
    block = render_binary_forecasts_block(anchored, language="English")
    assert "Market P(yes)" in block                  # 有锚点 → 追加市场列
    assert "30% (Δ-5pt)" in block                    # 隐含概率 + 分歧（点数）
    assert block.count("| F2 |") == 1 and "| — |" in block  # 无锚点行显示占位符

    plain = {
        "binary_forecasts": [
            {"id": "F1", "statement": "S", "probability": 0.5,
             "resolution_criteria": "m > 1 by 2027", "theme": "t"},
        ],
    }
    assert "Market P(yes)" not in render_binary_forecasts_block(plain, language="English")

    zh = render_binary_forecasts_block(anchored, language="Chinese")
    assert "市场隐含 P(yes)" in zh                   # 中文列头随输出语言


def test_derive_forecast_spine_injects_market_block():
    scen = {
        "headline": "h", "horizon": "2027",
        "scenarios": [
            {"name": "A", "probability": 0.6, "summary": "s", "key_drivers": [],
             "resolution_criteria": "x > 1 by 2027"},
            {"name": "B", "probability": 0.4, "summary": "s", "key_drivers": [],
             "resolution_criteria": "x <= 1 by 2027"},
        ],
        "confidence": "medium",
    }
    fake = FakeLLMClient(json_responses=[dict(scen) for _ in range(6)])
    out = derive_forecast_spine(fake, central_question="q", market_block=_MARKET_PACK)
    assert out["scenarios"]
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "预测市场隐含概率" in prompt and "mkt-1" in prompt
    assert "校准锚点" in prompt

    fake2 = FakeLLMClient(json_responses=[dict(scen) for _ in range(6)])
    derive_forecast_spine(fake2, central_question="q")
    assert "预测市场隐含概率" not in fake2.calls[0]["messages"][0]["content"]  # 空块 → 提示词不变
