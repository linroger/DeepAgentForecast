"""Golden tests for structured forecast extraction + citation audit (EXECPLAN2 I-3-0/I-3-1)."""

from app.services.forecast_extractor import (
    _binary_quality,
    anchor_binaries_to_markets,
    apply_horizon_consistency,
    audit_citation_grounding,
    build_market_comparison,
    derive_forecast_spine,
    enforce_market_divergence,
    extract_binary_forecasts,
    extract_structured_forecast,
    parse_requirement_years,
    render_binary_forecasts_block,
    render_forecast_spine_block,
    render_resolution_block,
    slice_head_tail,
)
from app.services.ensemble import pool_binary_forecasts
from tests.conftest import FakeLLMClient


def test_audit_citation_grounding_counts_and_coverage():
    md = (
        "# 标题\n"
        "市场份额将达到 45% [S1]。\n"          # cited quantitative
        "营收增长 30%。\n"                      # uncited quantitative
        "到 2030年 格局重塑【S3】。\n"          # cited (CJK marker + year)
        "这是一句没有数字的话。\n"             # not quantitative
    )
    a = audit_citation_grounding(md)
    assert a["quantitative_claims"] == 3
    assert a["cited"] == 2
    assert a["coverage"] == round(2 / 3, 3)
    assert any("营收增长 30%" in s for s in a["unsupported_samples"])


def test_audit_empty_is_full_coverage():
    assert audit_citation_grounding("no numbers here")["coverage"] == 1.0


def test_extract_structured_forecast_normalizes_probabilities():
    fake = FakeLLMClient(json_responses=[{
        "headline": "存储芯片三强争霸",
        "horizon": "2030",
        "scenarios": [
            {"name": "三星领先", "probability": 3, "summary": "...", "key_drivers": ["HBM"],
             "resolution_criteria": "2030 年三星 DRAM 份额>40%"},
            {"name": "格局僵持", "probability": 1, "summary": "...", "key_drivers": [],
             "resolution_criteria": "无单一厂商>40%"},
        ],
        "key_uncertainties": ["出口管制"],
        "confidence": "medium",
    }])
    out = extract_structured_forecast("some report markdown", fake)
    probs = [s["probability"] for s in out["scenarios"]]
    assert abs(sum(probs) - 1.0) < 1e-6      # normalized to sum 1
    assert probs[0] == 0.75 and probs[1] == 0.25
    assert out["schema_version"] == 1
    assert out["confidence"] == "medium"
    assert fake.calls and fake.calls[0]["kind"] == "chat_json"


def test_extract_handles_garbage_reply():
    fake = FakeLLMClient(json_responses=[{"nonsense": True}])
    out = extract_structured_forecast("x", fake)
    assert out["scenarios"] == []
    assert out["confidence"] == "medium"  # safe default


# ----------------------------------------------- NEXTSTEPS P0-1: forecast spine
def test_derive_forecast_spine_from_signals_not_prose():
    fake = FakeLLMClient(json_responses=[{
        "headline": "X",
        "horizon": "2030",
        "scenarios": [
            {"name": "情景A", "probability": 2, "resolution_criteria": "到2030 A>50%"},
            {"name": "维持现状", "probability": 2, "resolution_criteria": "无变化"},
        ],
        "confidence": "high",
    }])
    out = derive_forecast_spine(
        fake, central_question="谁会赢", horizon="2030",
        situation_brief="某态势简报",
        forecast_inputs="参考类基率: 历史延续率 70%", signal_pack="Top actor: NVIDIA",
    )
    assert out["derived_from"] == "spine"
    probs = [s["probability"] for s in out["scenarios"]]
    assert abs(sum(probs) - 1.0) < 1e-6 and probs == [0.5, 0.5]
    # the spine prompt is seeded by SIGNALS (inputs + question), not finished prose
    msg = fake.calls[0]["messages"][0]["content"]
    assert "参考类基率" in msg and "Top actor" in msg and "谁会赢" in msg


def test_render_forecast_spine_block_empty_when_no_scenarios():
    assert render_forecast_spine_block(None) == ""
    assert render_forecast_spine_block({"scenarios": []}) == ""


def test_render_forecast_spine_block_lists_scenarios_with_pct():
    block = render_forecast_spine_block({
        "headline": "核心结论X", "horizon": "2030", "confidence": "medium",
        "scenarios": [
            {"name": "情景A", "probability": 0.6, "resolution_criteria": "A>50%"},
            {"name": "维持现状", "probability": 0.4, "resolution_criteria": "无变化"},
        ],
    })
    assert "预测骨架" in block
    assert "60%" in block and "情景A" in block
    assert "40%" in block and "维持现状" in block
    assert "判定" in block


# ------------------------------------------ NEXTSTEPS P2-2: resolution section
def test_render_resolution_block_lists_criteria_and_indicators():
    fc = {"scenarios": [
        {"name": "情景A", "probability": 0.6, "resolution_criteria": "2030 A 份额>50%"},
        {"name": "维持现状", "probability": 0.4, "resolution_criteria": "无重大变化"},
    ]}
    inds = [{"indicator": "DRAM 份额", "date_or_trigger": "2030Q4", "discriminates": "情景A"}]
    block = render_resolution_block(fc, inds)
    assert "如何验证本预测" in block
    assert "情景A" in block and "2030 A 份额>50%" in block
    assert "DRAM 份额" in block and "2030Q4" in block          # indicator table


def test_render_resolution_block_empty_without_scenarios():
    assert render_resolution_block(None) == ""
    assert render_resolution_block({"scenarios": []}) == ""


# ---------------------------------------- RQ-6: requirement-horizon consistency
def _binaries(*years):
    return [{"id": f"F{i}", "statement": f"Event {i} happens by {y}",
             "probability": 0.7, "horizon_year": y}
            for i, y in enumerate(years, start=1)]


def test_parse_requirement_years_filters_historical_context():
    txt = "Since the 2008 crisis tariffs rose. Forecast the November 2026 US midterm elections."
    assert parse_requirement_years(txt, now_year=2026) == [2026]   # 2008 = context, not a target
    assert parse_requirement_years("no target date given", now_year=2026) == []
    assert parse_requirement_years(None, now_year=2026) == []


def test_horizon_mismatch_flag_merges_into_quality():
    fc = {"binary_forecasts": _binaries(2028, 2028),
          "quality": {"vague_criteria": ["X"]}}
    out = apply_horizon_consistency(fc, "Forecast the 2026 US midterm elections.", now_year=2026)
    hm = out["quality"]["horizon_mismatch"]
    assert hm["requirement_years"] == [2026]
    assert hm["binary_years"] == [2028]
    assert "no overlap" in hm["detail"]
    assert out["quality"]["vague_criteria"] == ["X"]   # merged, never overwritten


def test_horizon_year_read_from_statement_when_field_missing():
    fc = {"binary_forecasts": [{"id": "F1", "statement": "Tariffs exceed 10% by 2028",
                                "probability": 0.6, "horizon_year": None}]}
    out = apply_horizon_consistency(fc, "the 2026 midterms", now_year=2026)
    assert out["quality"]["horizon_mismatch"]["binary_years"] == [2028]


def test_horizon_overlap_or_unparseable_adds_no_flag():
    # any overlap between brief years and binary resolution years → consistent, untouched
    out = apply_horizon_consistency({"binary_forecasts": _binaries(2026, 2028)},
                                    "the 2026 midterms", now_year=2026)
    assert "quality" not in out
    # brief without a parseable target year → no flag (degrade-safe)
    assert "quality" not in apply_horizon_consistency(
        {"binary_forecasts": _binaries(2028)}, "no target date given", now_year=2026)
    # no binaries / no binary-side years → no flag
    assert "quality" not in apply_horizon_consistency(
        {"binary_forecasts": []}, "by 2026", now_year=2026)


def test_horizon_check_disabled_by_flag(monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, "FORECAST_HORIZON_CHECK", False, raising=False)
    fc = {"binary_forecasts": _binaries(2028)}
    assert "quality" not in apply_horizon_consistency(fc, "the 2026 midterms", now_year=2026)


# ---------------------------------------------------- RQ-2: slice_head_tail math
def test_slice_head_tail_keeps_head_and_tail():
    text = "A" * 100 + "B" * 100                      # 200 chars, head=A tail=B
    out = slice_head_tail(text, budget=100, head_ratio=0.6)
    assert out.startswith("A" * 60)                    # head 60% of budget
    assert out.endswith("B" * 40)                      # tail 40% of budget
    assert "…(中段略)…" in out                          # elision marker between segments


def test_slice_head_tail_noop_when_within_budget():
    assert slice_head_tail("short", budget=1000) == "short"
    assert slice_head_tail("abc", budget=0) == "abc"   # non-positive budget → unchanged
    assert slice_head_tail("", budget=10) == ""


# ------------------------------------------------- RQ-2: theme cardinality flag
def test_binary_quality_flags_single_theme():
    single = [{"probability": 0.8, "theme": "ai", "criteria_sharp": True},
              {"probability": 0.2, "theme": "ai", "criteria_sharp": True}]
    q1 = _binary_quality(single, min_count=2)
    assert q1["theme_cardinality"] == 1
    assert any("single theme" in s for s in q1["issues"])
    multi = [{"probability": 0.8, "theme": "ai", "criteria_sharp": True},
             {"probability": 0.2, "theme": "trade", "criteria_sharp": True}]
    q2 = _binary_quality(multi, min_count=2)
    assert q2["theme_cardinality"] == 2
    assert not any("single theme" in s for s in q2["issues"])


# ----------------------------------------- PM-2: deterministic market anchoring
_ANCHOR_MARKETS = [
    {"market_id": "mkt-1", "question": "Tariffs > 10%?", "implied_yes_prob": 0.30,
     "url": "https://polymarket.com/event/tariffs", "end_date": "2028-12-31"},
]


def test_anchor_binaries_to_markets_backfills_rich_anchor():
    binaries = [
        {"id": "F1", "statement": "US tariff rate averages over 10% 2026-2028",
         "probability": 0.25, "adjustment_rationale": "base rate"},
        {"id": "F2", "statement": "AI capex exceeds $500B in 2027",
         "probability": 0.80, "adjustment_rationale": ""},
    ]
    fake = FakeLLMClient(json_responses=[{"matches": [
        {"forecast_id": "F1", "market_id": "mkt-1", "resolution_equivalence": "exact", "confidence": 0.9},
        {"forecast_id": "F2", "market_id": None, "resolution_equivalence": "loose", "confidence": 0.2},
    ]}])
    n = anchor_binaries_to_markets(binaries, _ANCHOR_MARKETS, fake)
    assert n == 1
    a = binaries[0]["market_anchor"]
    assert a["market_id"] == "mkt-1" and a["question"] == "Tariffs > 10%?"
    # implied 以我们的快照价为准；price_at_research 同源；divergence 本地计算。
    assert a["implied_yes_prob"] == 0.30 and a["price_at_research"] == 0.30
    assert a["divergence"] == round(0.25 - 0.30, 4)
    assert a["url"].endswith("/tariffs") and a["endDate"] == "2028-12-31"
    assert a["resolution_equivalence"] == "exact" and a["match_confidence"] == 0.9
    assert "market_anchor" not in binaries[1]           # null market_id → no anchor


def test_anchor_skips_loose_match_below_min_equivalence():
    binaries = [{"id": "F1", "statement": "X happens", "probability": 0.5}]
    fake = FakeLLMClient(json_responses=[{"matches": [
        {"forecast_id": "F1", "market_id": "mkt-1", "resolution_equivalence": "loose", "confidence": 0.9}]}])
    assert anchor_binaries_to_markets(binaries, _ANCHOR_MARKETS, fake) == 0
    assert "market_anchor" not in binaries[0]           # loose < default 'near' → dropped


def test_anchor_no_markets_or_bad_json_leaves_no_anchor():
    binaries = [{"id": "F1", "statement": "X happens", "probability": 0.5}]
    # no markets → no LLM call, no anchor
    assert anchor_binaries_to_markets(binaries, [], FakeLLMClient()) == 0
    # malformed reply → today's behavior (no anchors)
    assert anchor_binaries_to_markets(
        binaries, _ANCHOR_MARKETS, FakeLLMClient(json_responses=[{"nope": 1}])) == 0
    assert "market_anchor" not in binaries[0]


def test_anchor_disabled_by_flag(monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, "FORECAST_MARKET_ANCHORING", False, raising=False)
    binaries = [{"id": "F1", "statement": "X happens", "probability": 0.5}]
    fake = FakeLLMClient(json_responses=[{"matches": [
        {"forecast_id": "F1", "market_id": "mkt-1", "resolution_equivalence": "exact", "confidence": 1.0}]}])
    assert anchor_binaries_to_markets(binaries, _ANCHOR_MARKETS, fake) == 0
    assert fake.calls == []                              # flag off → no LLM call at all


# --------------------------------------------------- PM-2: 10pp divergence rule
def _divergent_binary():
    return {"id": "F1", "statement": "X happens", "probability": 0.20,
            "adjustment_rationale": "base rate says low",
            "market_anchor": {"market_id": "m", "implied_yes_prob": 0.55,
                              "divergence": round(0.20 - 0.55, 4)}}


def test_enforce_market_divergence_revises_when_rationale_cites_market():
    b = _divergent_binary()
    fake = FakeLLMClient(json_responses=[{"revisions": [
        {"id": "F1", "probability": 0.35,
         "adjustment_rationale": "The market implies 55%; I move up but it overweights the base case."}]}])
    assert enforce_market_divergence([b], fake) == 1
    assert b["probability"] == 0.35
    assert b["market_anchor"]["divergence"] == round(0.35 - 0.55, 4)   # recomputed


def test_enforce_market_divergence_rejects_silent_move():
    b = _divergent_binary()
    # revision moves the probability but never mentions the market → rejected, prob untouched
    fake = FakeLLMClient(json_responses=[{"revisions": [
        {"id": "F1", "probability": 0.55, "adjustment_rationale": "just moved it higher"}]}])
    assert enforce_market_divergence([b], fake) == 0
    assert b["probability"] == 0.20                     # never silently moved


def test_enforce_market_divergence_skips_when_already_cited():
    b = _divergent_binary()
    b["adjustment_rationale"] = "market prices 55% but I disagree because of the tail risk"
    fake = FakeLLMClient()                              # no responses queued
    assert enforce_market_divergence([b], fake) == 0
    assert fake.calls == []                             # already explained → no revision call


def test_enforce_market_divergence_ignores_within_10pp():
    b = _divergent_binary()
    b["probability"] = 0.50
    b["market_anchor"]["divergence"] = round(0.50 - 0.55, 4)   # |−0.05| <= 0.10
    fake = FakeLLMClient()
    assert enforce_market_divergence([b], fake) == 0
    assert fake.calls == []


def test_build_market_comparison_flags_10pp_breaches():
    binaries = [
        {"id": "F1", "statement": "X", "probability": 0.20,
         "adjustment_rationale": "the market prices 55%",
         "market_anchor": {"market_id": "m", "question": "Q", "implied_yes_prob": 0.55,
                           "price_at_research": 0.55, "divergence": -0.35}},
        {"id": "F2", "statement": "Y", "probability": 0.5},          # no anchor
    ]
    mc = build_market_comparison(binaries)
    assert mc["anchored_count"] == 1
    row = mc["comparisons"][0]
    assert row["forecast_id"] == "F1" and row["exceeds_10pp"] is True
    assert row["abs_divergence"] == 0.35 and row["rationale_cites_market"] is True


def test_extract_binary_forecasts_anchors_and_emits_comparison():
    markets = [{"market_id": "mkt-1", "question": "Tariffs > 10%?", "implied_yes_prob": 0.30,
                "url": "https://polymarket.com/event/t", "end_date": "2028-12-31"}]
    fake = FakeLLMClient(json_responses=[
        {"binary_forecasts": [
            {"id": "F1", "statement": "US tariff averages over 10% 2026-2028", "probability": 0.25,
             "resolution_criteria": "USITC > 10% by 2028", "theme": "trade", "horizon_year": 2028,
             "adjustment_rationale": "base rate"},
            {"id": "F2", "statement": "AI capex exceeds $500B in 2027", "probability": 0.80,
             "resolution_criteria": "capex > $500B in 2027", "theme": "ai", "horizon_year": 2027,
             "adjustment_rationale": "trend"},
        ]},
        {"matches": [                                    # deterministic anchoring call
            {"forecast_id": "F1", "market_id": "mkt-1", "resolution_equivalence": "exact", "confidence": 0.9}]},
    ])
    out = extract_binary_forecasts("dossier", fake, min_count=2, language="English",
                                   market_pack="table", markets=markets)
    f1 = next(b for b in out["binary_forecasts"] if b["id"] == "F1")
    assert f1["market_anchor"]["market_id"] == "mkt-1"
    assert f1["market_anchor"]["implied_yes_prob"] == 0.30          # our snapshot price
    assert "market_comparison" in out and out["market_comparison"]["anchored_count"] == 1
    # F1 divergence 0.25-0.30 = -0.05 (<=10pp) → no revision call was needed
    assert "market_anchor" not in next(b for b in out["binary_forecasts"] if b["id"] == "F2")


# ------------------------------------------------- ITEM 12: multi-model ensemble
def _ensemble_config(monkeypatch, *, models="modelb", extremize=None, threshold=0.15):
    """Configure the multi-model ensemble deterministically for a test.

    ``extremize=None`` → arithmetic-mean pooling (predictable math); contrarian /
    sim-sensitivity top-ups disabled so the primary makes exactly one draw call."""
    from app.config import Config
    monkeypatch.setattr(Config, "FORECAST_ENSEMBLE_MODELS", models, raising=False)
    monkeypatch.setattr(Config, "ENSEMBLE_EXTREMIZE_A", extremize, raising=False)
    monkeypatch.setattr(Config, "FORECAST_ENSEMBLE_SPREAD_THRESHOLD", threshold, raising=False)
    monkeypatch.setattr(Config, "FORECAST_BINARY_CONTRARIAN", False, raising=False)
    monkeypatch.setattr(Config, "FORECAST_SIM_SENSITIVITY", False, raising=False)


def _binary(bid, stmt, prob, theme="t1"):
    return {"id": bid, "statement": stmt, "probability": prob,
            "resolution_criteria": f"metric > 10% by 2027 ({bid})", "theme": theme,
            "horizon_year": 2027}


def test_ensemble_pools_across_models_and_flags_spread(monkeypatch):
    _ensemble_config(monkeypatch)
    primary = FakeLLMClient(provider="primary", json_responses=[
        {"binary_forecasts": [_binary("F1", "Alpha exceeds 10% by 2027", 0.20),
                              _binary("F2", "Beta exceeds 500 by 2027", 0.80, theme="t2")]}])
    secondary = FakeLLMClient(provider="modelb", json_responses=[
        {"binary_forecasts": [_binary("F1", "Alpha exceeds 10% by 2027", 0.60),
                              _binary("F2", "Beta exceeds 500 by 2027", 0.82, theme="t2")]}])
    out = extract_binary_forecasts(
        "dossier", primary, min_count=2, language="English",
        ensemble_client_factory=lambda name: {"modelb": secondary}.get(name))
    f1 = next(b for b in out["binary_forecasts"] if b["id"] == "F1")
    f2 = next(b for b in out["binary_forecasts"] if b["id"] == "F2")
    # statement-keyed match; arithmetic-mean pooling overrides the published probability
    assert f1["ensemble"]["models"] == ["primary", "modelb"]
    assert f1["ensemble"]["probs"] == [0.2, 0.6]
    assert f1["ensemble"]["pooled"] == 0.4
    assert f1["probability"] == 0.4
    assert f1["ensemble"]["spread"] > 0.15          # stdev([0.2,0.6]) ≈ 0.2828 → disagreement
    assert f2["ensemble"]["spread"] < 0.15          # stdev([0.8,0.82]) ≈ 0.0141 → agreement
    assert f2["probability"] == 0.81
    ens = out["binary_quality"]["ensemble"]
    assert ens["pooled_models"] == ["modelb"]
    assert ens["low_agreement"] == ["F1"]
    assert ens["skipped"] == []
    assert any("cross-model disagreement" in s for s in out["binary_quality"].get("issues", []))
    # the secondary client was invoked exactly once (one draw)
    assert sum(1 for c in secondary.calls if c["kind"] == "chat_json") == 1


def test_ensemble_id_matching_when_statements_differ(monkeypatch):
    _ensemble_config(monkeypatch, models="mb")
    primary = FakeLLMClient(provider="primary", json_responses=[
        {"binary_forecasts": [_binary("F1", "Alpha exceeds 10% by 2027", 0.30)]}])
    # different wording → statement key won't match, but id "F1" does (id fallback)
    secondary = FakeLLMClient(provider="mb", json_responses=[
        {"binary_forecasts": [_binary("F1", "Paraphrased alpha clause over ten percent", 0.50)]}])
    out = extract_binary_forecasts(
        "d", primary, min_count=1, language="English",
        ensemble_client_factory=lambda name: secondary if name == "mb" else None)
    f1 = out["binary_forecasts"][0]
    assert f1["ensemble"]["models"] == ["primary", "mb"]
    assert f1["ensemble"]["probs"] == [0.3, 0.5]
    assert f1["probability"] == 0.4


def test_ensemble_off_by_default_is_byte_identical(monkeypatch):
    # FORECAST_ENSEMBLE_MODELS empty → the entire ensemble path is skipped.
    from app.config import Config
    monkeypatch.setattr(Config, "FORECAST_ENSEMBLE_MODELS", "", raising=False)
    monkeypatch.setattr(Config, "FORECAST_BINARY_CONTRARIAN", False, raising=False)
    monkeypatch.setattr(Config, "FORECAST_SIM_SENSITIVITY", False, raising=False)
    primary = FakeLLMClient(provider="primary", json_responses=[
        {"binary_forecasts": [_binary("F1", "Alpha exceeds 10% by 2027", 0.30)]}])
    out = extract_binary_forecasts("d", primary, min_count=1, language="English")
    assert all("ensemble" not in b for b in out["binary_forecasts"])
    assert "ensemble" not in out["binary_quality"]
    assert out["binary_forecasts"][0]["probability"] == 0.3   # untouched primary


def test_ensemble_secondary_failure_is_skipped_never_blocks(monkeypatch):
    _ensemble_config(monkeypatch, models="badmodel")

    def _factory(name):
        raise RuntimeError(f"cannot build client for {name}")

    primary = FakeLLMClient(provider="primary", json_responses=[
        {"binary_forecasts": [_binary("F1", "Alpha exceeds 10% by 2027", 0.30)]}])
    out = extract_binary_forecasts("d", primary, min_count=1, language="English",
                                   ensemble_client_factory=_factory)
    f1 = out["binary_forecasts"][0]
    assert "ensemble" not in f1                      # nothing pooled
    assert f1["probability"] == 0.3                  # primary preserved
    ens = out["binary_quality"]["ensemble"]
    assert ens["skipped"] == ["badmodel"]
    assert ens["pooled_models"] == [] and ens["low_agreement"] == []


def test_pool_binary_unmatched_keeps_primary():
    primary = [{"id": "F1", "statement": "alpha", "probability": 0.30},
               {"id": "F2", "statement": "beta", "probability": 0.70}]
    secondary = {"mb": [{"id": "F1", "statement": "alpha", "probability": 0.50}]}  # only F1
    low = pool_binary_forecasts(primary, secondary, primary_model="primary",
                                extremize_a=None, spread_threshold=0.05)
    assert primary[0]["ensemble"]["probs"] == [0.3, 0.5]
    assert primary[0]["probability"] == 0.4
    assert "ensemble" not in primary[1]              # F2 had no secondary → keep primary
    assert primary[1]["probability"] == 0.70
    assert low == ["F1"]                             # stdev([0.3,0.5]) ≈ 0.1414 > 0.05


def test_render_binary_block_shows_pm_spread_and_low_agreement():
    forecast = {
        "binary_forecasts": [
            {"id": "F1", "statement": "Alpha", "probability": 0.40, "resolution_criteria": "x",
             "theme": "t1", "ensemble": {"models": ["a", "b"], "probs": [0.2, 0.6],
                                         "pooled": 0.4, "spread": 0.2828}},
            {"id": "F2", "statement": "Beta", "probability": 0.81, "resolution_criteria": "y",
             "theme": "t2", "ensemble": {"models": ["a", "b"], "probs": [0.8, 0.82],
                                         "pooled": 0.81, "spread": 0.0141}},
        ],
        "binary_quality": {"count": 2, "conviction_count": 1, "sharp_criteria_count": 0,
                           "ensemble": {"pooled_models": ["b"], "low_agreement": ["F1"],
                                        "spread_threshold": 0.15}},
    }
    md = render_binary_forecasts_block(forecast, language="English")
    assert "±28%" in md            # F1 spread rendered
    assert "⚠" in md               # F1 exceeds threshold → low-agreement marker
    assert "±1%" in md             # F2 spread rendered (tight)
    assert "cross-model disagreement" in md
    # a plain (non-ensemble) forecast is byte-unchanged (no ± column noise)
    plain = {"binary_forecasts": [{"id": "F1", "statement": "X", "probability": 0.5,
                                   "resolution_criteria": "z", "theme": "t"}]}
    assert "±" not in render_binary_forecasts_block(plain, language="English")
