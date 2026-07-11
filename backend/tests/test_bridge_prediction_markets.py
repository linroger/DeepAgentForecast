"""deerflow_bridge/deerflow_research.py 纯 helper 的离线单测（PM-1 / INT-2 / RQ-6）。

桥脚本运行在 DeerFlow 自己的 venv 里、模块级只 import 标准库（``deerflow`` 相关 import
都在函数体内），因此可从 backend 测试里直接 import 做纯函数测试——只需把 deerflow_bridge
目录挂到 sys.path（backend conftest 只挂了 backend/，不含桥目录）。全部纯函数、零网络、零 LLM。
"""

import os
import sys
import json

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "deerflow_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

import deerflow_research as d  # noqa: E402


def test_agentic_delegation_prompt_uses_actual_lane_cap(monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENTIC_SEARCH", "true")
    monkeypatch.setenv("DEER_FLOW_MAX_CONCURRENT_SUBAGENTS", "2")
    d._set_agentic_delegation(True)
    try:
        prompt = d._agentic_delegation_block(False)
        assert "at most 2 parallel tasks" in prompt
        assert "3–5" not in prompt and "3-5" not in prompt
        assert d._stream_model_lease_weight() == 1
    finally:
        d._set_agentic_delegation(False)


# ---------------------------------------------------------------- PM-1 tokenizer / phrases

def test_salient_phrases_keeps_four_digit_years():
    """4 位年份必须作为独立 token 保留（旧的 `[A-Za-z]…` 起始类会整个吃掉 '2026'）。"""
    phrases = d._pm_salient_phrases("Who will win the 2026 US House control election?")
    joined = " ".join(phrases)
    assert "2026" in joined
    # 年份不应被丢弃或与前词错误吞并成无年份短语
    assert any(p.strip().startswith("2026") or " 2026" in p for p in phrases)


def test_salient_phrases_blacklists_generic_words():
    """泛化名词（key/factors/outcome/analysis/impact）作为停用词断句，不进入短语。"""
    phrases = d._pm_salient_phrases("Key factors and outcome analysis for 2028 impact")
    joined = " ".join(phrases).lower()
    for bad in ("key", "factors", "outcome", "analysis", "impact"):
        assert bad not in joined.split()
    assert "2028" in joined  # 年份仍保留


def test_salient_phrases_clause_boundary_truncation():
    """子句边界（逗号）强制断句：'House control, Senate races' 不得合成跨子句短语。"""
    phrases = d._pm_salient_phrases("House control, Senate races")
    # 不允许出现同时含 control 和 Senate 的单一短语（说明跨了逗号）
    assert not any("control" in p.lower() and "senate" in p.lower() for p in phrases)


# ---------------------------------------------------------------- PM-1 query derivation

def test_derive_queries_cap_is_twelve():
    q = d._pm_derive_queries(
        "A very long forecasting question about many distinct topics and races",
        hot_topics=[f"topic{i}" for i in range(20)],
        actor_names=[f"actor{i}" for i in range(20)],
    )
    assert len(q) <= 12


def test_derive_queries_reserves_actor_slots():
    """问题短语+热点即使能占满 12 名额，也必须为 ≥2 个 actor 名留位。"""
    q = d._pm_derive_queries(
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi",
        hot_topics=[f"hot{i}" for i in range(20)],
        actor_names=["Mike Johnson", "Hakeem Jeffries", "Chuck Schumer"],
    )
    assert "Mike Johnson" in q
    assert "Hakeem Jeffries" in q
    assert len(q) <= 12


def test_derive_queries_no_actors_is_fine():
    q = d._pm_derive_queries("2026 US House control", hot_topics=None, actor_names=None)
    assert q and all(isinstance(x, str) for x in q)


# ---------------------------------------------------------------- PM-1 LLM query parsing

def test_parse_market_queries_json_array():
    out = d._parse_market_queries('["House control 2026", "Ohio Senate winner"]')
    assert out == ["House control 2026", "Ohio Senate winner"]


def test_parse_market_queries_line_fallback():
    out = d._parse_market_queries("1. House control 2026\n2) Ohio Senate winner\n- Fed rate cut")
    assert out == ["House control 2026", "Ohio Senate winner", "Fed rate cut"]


def test_parse_market_queries_embedded_json_and_dedup():
    out = d._parse_market_queries('Here you go: ["A market", "A market", "B market"] done')
    assert out == ["A market", "B market"]


def test_parse_market_queries_empty_returns_empty():
    assert d._parse_market_queries("") == []
    assert d._parse_market_queries("   ") == []


def test_parse_market_queries_truncates_long():
    out = d._parse_market_queries('["one two three four five six seven eight"]', max_words=6)
    assert out == ["one two three four five six"]


# ---------------------------------------------------------------- PM-1 relevance gate

def test_parse_relevance_scores_dict():
    got = d._parse_relevance_scores('{"111": 8, "222": 3, "999": 5}', ["111", "222"])
    assert got == {"111": 8.0, "222": 3.0}  # 999 未知 id 被过滤


def test_parse_relevance_scores_list_shape():
    got = d._parse_relevance_scores('[{"id":"111","score":7},{"market_id":"222","relevance":2}]',
                                    ["111", "222"])
    assert got == {"111": 7.0, "222": 2.0}


def test_parse_relevance_scores_unparseable_returns_empty():
    assert d._parse_relevance_scores("not json at all", ["1"]) == {}
    assert d._parse_relevance_scores("", ["1"]) == {}


def test_apply_relevance_gate_drops_and_ranks():
    markets = [
        {"market_id": "low", "volume": 999.0},
        {"market_id": "hi", "volume": 10.0},
        {"market_id": "mid", "volume": 50.0},
    ]
    scores = {"low": 2.0, "hi": 9.0, "mid": 6.0}
    kept = d._apply_relevance_gate(markets, scores, 5.0)
    ids = [m["market_id"] for m in kept]
    assert ids == ["hi", "mid"]  # low(2)<5 丢弃；按 relevance 降序
    assert all("relevance_score" in m for m in kept)


def test_apply_relevance_gate_empty_scores_keeps_all_by_volume():
    markets = [{"market_id": "a", "volume": 5.0}, {"market_id": "b", "volume": 50.0}]
    kept = d._apply_relevance_gate(markets, {}, 5.0)
    assert [m["market_id"] for m in kept] == ["b", "a"]  # 无分 → 全保留，按 volume 降序
    assert all("relevance_score" not in m for m in kept)


def test_market_env_uses_canonical_relevance_and_per_query_names(monkeypatch):
    monkeypatch.setenv("PM_MIN_RELEVANCE", "2")
    monkeypatch.setenv("PREDICTION_MARKETS_MIN_RELEVANCE", "7")
    monkeypatch.setenv("PREDICTION_MARKETS_PER_QUERY", "17")
    assert d._pm_min_relevance() == 7.0
    assert d._pm_per_query() == 17


def test_market_snapshot_diagnostics_distinguish_transport_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(d, "_polymarket_get", fail)
    diagnostics = {}
    assert d._pm_snapshot(["AI bubble", "US recession"], diagnostics=diagnostics) == []
    assert diagnostics == {
        "attempted_query_count": 2,
        "successful_query_count": 0,
        "transport_failure_count": 2,
    }


# ---------------------------------------------------------------- PM-1 market normalization enrich

def test_normalize_market_enriches_event_url_and_signals():
    raw = {
        "id": "42", "question": "Will X happen?",
        "outcomes": '["Yes","No"]', "outcomePrices": '["0.30","0.70"]',
        "volume": 5000, "liquidity": 100, "closed": False,
        "endDate": "2026-11-03T00:00:00Z", "oneDayPriceChange": 0.05,
        "bestBid": 0.29, "bestAsk": 0.31,
    }
    row = d._pm_normalize_market(raw, matched_query="x", min_volume=200,
                                 event_title="US House", event_slug="us-house-2026")
    assert row["event_url"] == "https://polymarket.com/event/us-house-2026"
    assert row["end_date"].startswith("2026-11-03")
    assert row["one_day_price_change"] == 0.05
    assert row["best_bid"] == 0.29 and row["best_ask"] == 0.31


def test_normalize_market_missing_signals_degrade():
    raw = {
        "id": "1", "question": "Q?", "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]', "volume": 5000, "closed": False,
    }
    row = d._pm_normalize_market(raw, matched_query="x", min_volume=200)
    assert "event_url" not in row and "best_bid" not in row  # 缺失字段不写键


# ---------------------------------------------------------------- INT-2 URL / tool-arg validation

def test_is_valid_http_url():
    assert d._is_valid_http_url("https://example.com/a")
    assert d._is_valid_http_url("http://x.io")
    assert d._is_valid_http_url("https://en.wikipedia.org/wiki/AI")
    assert not d._is_valid_http_url("notaurl")
    assert not d._is_valid_http_url("ftp://x.com")
    assert not d._is_valid_http_url("https://")
    assert not d._is_valid_http_url("https://en")
    assert not d._is_valid_http_url("https://en.wikipedia.org")
    assert not d._is_valid_http_url("https://en.wikipedia.org/wiki/St")
    assert not d._is_valid_http_url("https://en.wikipedia.org/wiki/ASML_H")
    assert not d._is_valid_http_url("https://en.wikipedia.org/wiki/Anduril_")
    assert not d._is_valid_http_url("")


def test_repair_url():
    assert d._repair_url("polymarket.com/event/x") == "https://polymarket.com/event/x"
    assert d._repair_url("//cdn.x.com/a") == "https://cdn.x.com/a"
    assert d._repair_url("foo bar") == ""       # 含空格无法修复
    assert d._repair_url("just-a-word") == ""   # 非域名形状


def test_title_from_url_prefers_human_readable_content_slug():
    assert d._title_from_url(
        "https://en.wikipedia.org/wiki/AI_Action_Plan"
    ) == "AI Action Plan"
    assert d._title_from_url("https://example.com") == "example.com"


def test_validate_tool_args_web_search_query_len():
    _, ok, _ = d._validate_tool_args("web_search", {"query": "ab"})
    assert ok is False
    _, ok, _ = d._validate_tool_args("web_search", {"query": "abcd"})
    assert ok is True


def test_validate_tool_args_web_fetch_repairs_url():
    args, ok, _ = d._validate_tool_args("web_fetch", {"url": "polymarket.com/x"})
    assert ok is True and args["url"] == "https://polymarket.com/x"


def test_validate_tool_args_web_fetch_rejects_garbage():
    _, ok, why = d._validate_tool_args("web_fetch", {"url": "###"})
    assert ok is False and "scheme" in why


def test_validate_tool_args_passthrough_other_tools():
    args = {"path": "/tmp"}
    out, ok, _ = d._validate_tool_args("read_file", args)
    assert ok is True and out is args


# ---------------------------------------------------------------- INT-2 sources.json URL grounding

def test_merge_fetched_into_sources_drops_urlless_and_repairs():
    d._reset_fetched_sources()
    grounded, dropped = d.merge_fetched_into_sources([
        {"title": "no url", "url": ""},               # 无 URL → 丢
        {"title": "bare host", "url": "example.com/a"},  # 裸 host → 修复保留
        {"title": "good", "url": "https://good.org/x"},
    ])
    urls = {s["url"] for s in grounded}
    assert "https://example.com/a" in urls
    assert "https://good.org/x" in urls
    assert dropped >= 1  # 无 URL 的那条被丢


# ---------------------------------------------------------------- RQ-6 brief-drift detection

def test_detect_year_drift_flags_mismatch():
    drift = d._detect_year_drift(
        "Who wins the 2026 US midterms?",
        "The 2028 presidential race dominates. 2028 primaries. 2028 again everywhere.",
    )
    assert drift is not None
    assert drift["notes_year"] == "2028"
    assert drift["brief_years"] == ["2026"]


def test_detect_year_drift_no_mismatch_when_aligned():
    assert d._detect_year_drift(
        "Who wins the 2026 US midterms?",
        "The 2026 midterms with some 2024 historical context.",
    ) is None


def test_detect_year_drift_none_without_brief_year():
    assert d._detect_year_drift("Who wins the election?", "The 2028 race.") is None


# ---------------------------------------------------------------- PM-4 prompt injection plumbing

def test_market_pricing_block_injection_roundtrip():
    d._set_market_pricing_block("")
    assert d._market_pricing_prompt_block() == ""
    block = d._pm_render_pricing_block(
        [{"market_id": "1", "question": "Will X?", "implied_yes_prob": 0.42, "volume": 1000.0}],
        as_of="2026-07-06T00:00:00+00:00",
    )
    assert "42%" in block and "Will X?" in block
    d._set_market_pricing_block(block)
    prompt = d.build_research_prompt("some question", "standard", None)
    assert "Will X?" in prompt and "42%" in prompt
    d._set_market_pricing_block("")  # 复位，勿泄漏到其它测试


# ================================================================ SCALE-5 / RQ-3 / PM-6 pure helpers

# ---------------------------------------------------------------- SCALE-5 universal source tiering

def test_universal_tiering_fills_fetched_default_s3(monkeypatch):
    """域名表命不中、模型也没给 tier 的**已抓取**来源 → 拿基线 tier（默认 S3），而非留 untiered。"""
    monkeypatch.delenv("RESEARCH_UNIVERSAL_TIERING", raising=False)
    monkeypatch.delenv("RESEARCH_DEFAULT_SOURCE_TIER", raising=False)
    d._reset_fetched_sources()
    d._merge_pending_fetches([{"url": "https://unknown-domain-xyz.example/a", "ok": True}])
    grounded, _dropped = d.merge_fetched_into_sources([])  # 无模型来源
    row = next(r for r in grounded if "unknown-domain-xyz.example" in r["url"])
    assert row["source_origin"] == "fetched"
    assert row["tier"] == "S3"
    d._reset_fetched_sources()


def test_universal_tiering_off_keeps_untiered(monkeypatch):
    """RESEARCH_UNIVERSAL_TIERING=false → untiered 抓取源保持无 tier（旧行为）。"""
    monkeypatch.setenv("RESEARCH_UNIVERSAL_TIERING", "false")
    d._reset_fetched_sources()
    d._merge_pending_fetches([{"url": "https://unknown-domain-xyz.example/b", "ok": True}])
    grounded, _ = d.merge_fetched_into_sources([])
    row = next(r for r in grounded if "unknown-domain-xyz.example" in r["url"])
    assert str(row.get("tier") or "").strip().upper() not in ("S1", "S2", "S3", "S4")
    d._reset_fetched_sources()


def test_universal_tiering_preserves_domain_and_model_tier(monkeypatch):
    """域名映射（congress.gov→S1）与模型自愿 tier 都胜过兜底默认（不被覆盖）。"""
    monkeypatch.setenv("RESEARCH_DEFAULT_SOURCE_TIER", "S2")
    d._reset_fetched_sources()
    d._merge_pending_fetches([
        {"url": "https://www.congress.gov/bill/x", "ok": True},      # S1 域名
        {"url": "https://random-blog.example/post", "ok": True},     # 未知域名 + 模型给 S1
    ])
    grounded, _ = d.merge_fetched_into_sources([
        {"url": "https://random-blog.example/post", "tier": "S1"},
    ])
    by = {r["url"]: r for r in grounded}
    gov = next(r for u, r in by.items() if "congress.gov" in u)
    assert gov["tier"] == "S1"           # 域名映射胜过默认 S2
    blog = next(r for u, r in by.items() if "random-blog.example" in u)
    assert blog["tier"] == "S1"          # 模型自愿 tier 保留，未被默认 S2 覆盖
    d._reset_fetched_sources()


def test_default_source_tier_rejects_s4_and_garbage(monkeypatch):
    monkeypatch.setenv("RESEARCH_DEFAULT_SOURCE_TIER", "S4")
    assert d._default_source_tier() == "S3"     # S4=reject-tier 不可作默认
    monkeypatch.setenv("RESEARCH_DEFAULT_SOURCE_TIER", "garbage")
    assert d._default_source_tier() == "S3"
    monkeypatch.setenv("RESEARCH_DEFAULT_SOURCE_TIER", "S1")
    assert d._default_source_tier() == "S1"
    monkeypatch.delenv("RESEARCH_DEFAULT_SOURCE_TIER", raising=False)
    assert d._default_source_tier() == "S3"     # 未设置 → S3


# ---------------------------------------------------------------- SCALE-5 adaptive-pass ceiling

def test_adaptive_passes_remaining_arithmetic():
    fixed = 1 + len(d.DEEP_RESEARCH_PHASES)          # 开场 + 固定相位
    assert d.adaptive_passes_remaining(0, 12) == 12 - fixed
    assert d.adaptive_passes_remaining(2, 12) == 12 - fixed - 2
    # 覆盖轮 + 固定 已 >= 总上限 → 0（绝不为负）
    assert d.adaptive_passes_remaining(10, 12) == 0
    # 总上限低于固定 pass → 0
    assert d.adaptive_passes_remaining(0, fixed - 1) == 0
    # 坏输入 → 0（不抛）
    assert d.adaptive_passes_remaining("x", 12) == 0
    assert d.adaptive_passes_remaining(0, None) == 0


# ---------------------------------------------------------------- RQ-3 report-judge parse fallback

def test_report_passes_none_and_nondict_are_passthrough():
    """无有效记分牌（None/非 dict）→ 不阻断（pass-through，回退发首稿）。"""
    assert d.report_passes(None) is True
    assert d.report_passes("not a dict") is True


def test_report_passes_empty_scores_uses_verdict():
    assert d.report_passes({"verdict": "PASS"}) is True
    assert d.report_passes({"verdict": "FAIL"}) is False
    assert d.report_passes({}) is True               # 无 verdict/无 scores → 非 FAIL → 放行


def test_report_passes_full_scorecard_gate():
    dims = d._REPORT_JUDGE_DIMS
    # 全 5 分 → PASS
    assert d.report_passes({"scores": {k: 5 for k in dims}}) is True
    # 非关键维 = 3（无维 <3、四关键维 ≥4、均分 ≥4）→ 仍 PASS
    sc = {k: 5 for k in dims}
    sc["quantitative_density"] = 3
    assert d.report_passes({"scores": sc}) is True
    # 关键维 <4 → FAIL
    sc2 = {k: 5 for k in dims}
    sc2["thesis_specificity"] = 3
    assert d.report_passes({"scores": sc2}) is False
    # 任一维 <3 → FAIL
    sc3 = {k: 5 for k in dims}
    sc3["quantitative_density"] = 2
    assert d.report_passes({"scores": sc3}) is False


def test_report_passes_strict_env(monkeypatch):
    """RESEARCH_REPORT_JUDGE_STRICT=true → 任一维 <4 即 FAIL。"""
    dims = d._REPORT_JUDGE_DIMS
    sc = {k: 5 for k in dims}
    sc["length_vs_target"] = 3       # 非关键维 3，宽松模式本应 PASS
    monkeypatch.setenv("RESEARCH_REPORT_JUDGE_STRICT", "true")
    assert d.report_passes({"scores": sc}) is False
    monkeypatch.setenv("RESEARCH_REPORT_JUDGE_STRICT", "false")
    assert d.report_passes({"scores": sc}) is True


def test_build_report_judge_prompt_is_json_only():
    p = d.build_report_judge_prompt("Will X happen in 2026?", None, "15,000–25,000 words")
    assert "thesis_specificity" in p and "citation_coverage" in p
    assert "PASS|FAIL" in p and "JSON" in p


# ---------------------------------------------------------------- PM-6 price-history parse

def test_pm_parse_price_history_shapes():
    data = {"history": [{"t": 1700000000, "p": 0.42}, {"t": 1700086400, "p": 0.5}]}
    assert d._pm_parse_price_history(data, days=0) == [
        {"t": 1700000000, "p": 0.42}, {"t": 1700086400, "p": 0.5}]
    # 少数形态直接是点数组
    assert d._pm_parse_price_history([{"t": 1, "p": 0.1}], days=0) == [{"t": 1, "p": 0.1}]
    # 脏点（缺 t/缺 p/非 dict）跳过
    assert d._pm_parse_price_history({"history": [{"t": None, "p": 0.1}, "x", {"p": 0.2}]}, days=0) == []
    # 非预期形状 → []
    assert d._pm_parse_price_history(None) == []
    assert d._pm_parse_price_history({"nope": 1}) == []


def test_pm_parse_price_history_days_trim():
    import time
    now = int(time.time())
    old = now - 100 * 86400          # 100 天前 → 被 90 天窗口裁掉
    recent = now - 10 * 86400        # 10 天前 → 保留
    out = d._pm_parse_price_history({"history": [{"t": old, "p": 0.3}, {"t": recent, "p": 0.6}]}, days=90)
    ts = [pt["t"] for pt in out]
    assert old not in ts and recent in ts


def test_pm_fetch_price_history_empty_token_no_network():
    """空 token → [] 且不触网（degrade-to-empty）。"""
    assert d._pm_fetch_price_history("") == []
    assert d._pm_fetch_price_history(None) == []


# ---------------------------------------------------------------- PM-6 clob_token_ids on normalize

def test_normalize_market_extracts_clob_token_ids():
    raw = {
        "id": "123", "question": "Will X happen?",
        "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]',
        "volume": 5000, "closed": False,
        "clobTokenIds": '["0xaaa","0xbbb"]',
    }
    row = d._pm_normalize_market(raw, matched_query="x", min_volume=200)
    assert row is not None
    assert row["clob_token_ids"] == ["0xaaa", "0xbbb"]
    # 缺 clobTokenIds → 键不出现（不造假）
    raw2 = dict(raw)
    raw2.pop("clobTokenIds")
    row2 = d._pm_normalize_market(raw2, matched_query="x", min_volume=200)
    assert "clob_token_ids" not in row2


# ---------------------------------------------------------------- LOOP-009 canonical in-loop market delivery

def test_market_report_match_prefers_machine_identity_and_is_conservative():
    market = {
        "market_id": "691340",
        "question": "AI bubble burst in 2026?",
        "implied_yes_prob": 0.1545,
        "url": "https://polymarket.com/event/ai-bubble-burst-in-2026",
    }
    assert d._market_is_cited_in_report(
        "The evidence diverges from market 691340, currently 15.45%.", market)
    assert d._market_is_cited_in_report(
        "See https://polymarket.com/event/ai-bubble-burst-in-2026 for the live quote.", market)
    assert not d._market_is_cited_in_report(
        "A celebrity election market has no bearing on semiconductor demand.", market)


def test_tool_candidate_loader_skips_bad_lines_and_keeps_latest_quote(tmp_path):
    path = tmp_path / d.PREDICTION_MARKET_CANDIDATES_FILENAME
    path.write_text(
        "not-json\n"
        + json.dumps({
            "captured_at": "2026-07-10T00:00:00Z", "queries": ["AI bubble"],
            "markets": [{"market_id": "691340", "implied_yes_prob": 0.10}],
        }) + "\n"
        + json.dumps({
            "captured_at": "2026-07-11T00:00:00Z", "queries": ["AI bubble 2026"],
            "markets": [{"market_id": "691340", "implied_yes_prob": 0.1545}],
        }) + "\n",
        encoding="utf-8",
    )

    rows = d._load_tool_market_candidates(tmp_path)

    assert len(rows) == 1
    assert rows[0]["implied_yes_prob"] == 0.1545
    assert rows[0]["captured_via"] == "prediction_market_search"
    assert rows[0]["tool_queries"] == ["AI bubble 2026"]


def test_collector_preserves_report_vetted_tool_market_when_refresh_has_no_queries(
    tmp_path, monkeypatch,
):
    market = {
        "market_id": "691340",
        "question": "AI bubble burst in 2026?",
        "implied_yes_prob": 0.1545,
        "volume": 2_310_000.0,
        "url": "https://polymarket.com/event/ai-bubble-burst-in-2026",
        "end_date": "2026-12-31T00:00:00Z",
    }
    (tmp_path / d.PREDICTION_MARKET_CANDIDATES_FILENAME).write_text(
        json.dumps({
            "captured_at": "2026-07-11T00:00:00Z",
            "queries": ["AI bubble 2026"], "markets": [market],
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(d, "_pm_resolve_queries", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(d, "score_market_relevance", lambda *_args, **_kwargs: {})
    monkeypatch.setenv("PREDICTION_MARKETS_ENABLED", "true")
    monkeypatch.setenv("PREDICTION_MARKETS_PRICE_HISTORY", "false")

    class Log:
        def __init__(self):
            self.rows = []

        def write(self, level, message):
            self.rows.append((level, message))

    meta = {}
    log = Log()
    report = "Our external calibration is Polymarket market 691340 at 15.45%."
    d._collect_prediction_markets(
        tmp_path, "Will the AI investment boom unwind in 2026?", report,
        meta, log, model_name="test",
    )

    payload = json.loads((tmp_path / d.PREDICTION_MARKETS_FILENAME).read_text(encoding="utf-8"))
    assert [row["market_id"] for row in payload["markets"]] == ["691340"]
    assert payload["status"]["tool_observation_count"] == 1
    assert payload["status"]["selected_count"] == 1
    assert payload["status"]["empty_reason"] is None
    assert meta["prediction_markets_count"] == 1
    assert "Prediction Market Signals" in (tmp_path / d.REPORT_FILENAME).read_text(encoding="utf-8")


def test_bridge_fanout_suppressed_when_harness_delegation_is_active(monkeypatch):
    monkeypatch.setattr(d, "_AGENTIC_DELEGATION", True)
    monkeypatch.setenv("RESEARCH_AGENTIC_SEARCH", "true")
    monkeypatch.setenv("RESEARCH_DEEP_FANOUT", "true")
    monkeypatch.delenv("RESEARCH_ALLOW_STACKED_FANOUT", raising=False)
    assert d._bridge_fanout_enabled() is False
    monkeypatch.setenv("RESEARCH_ALLOW_STACKED_FANOUT", "true")
    assert d._bridge_fanout_enabled() is True


def test_research_prompt_deterministically_activates_deep_research_skill():
    d._set_market_pricing_block("")
    prompt = d.build_research_prompt("Will X happen?", "standard", None)
    assert prompt.startswith("/deep-research\n")
