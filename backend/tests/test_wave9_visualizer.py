"""WAVE9：plotly-first 报告可视化重建 —— 新构建器族（二元点阵/时间线泳道/角色网络/气泡/
sunburst/量化点阵/驱动 tornado/争议哑铃）、ensemble 误差带、kaleido PNG 对、manifest v2
（items + skipped，无沉默跳过）、名字归一/去重助手，以及真实 handoff 工件的集成冒烟。

除标记 kaleido 的专项测试外全部关闭 PNG 导出（每次导出要起一次 Chromium）。离线、无 LLM。"""

import json
import os
import re

import pytest

from app.services import report_visualizer as rv
from app.services.report_visualizer import ReportVisualizer


# ─────────────────────────────────────────────────────────────────────────────
# 合成工件（键名与真实落盘一致；形状取自 pipe_f23527f7d903/handoff）
# ─────────────────────────────────────────────────────────────────────────────
FORECAST = {
    "headline": "Semis ≥ $1.45T by 2030",
    "scenarios": [
        {"name": "Supercycle holds", "probability": 0.45, "p_low": None, "p_high": None},
        {"name": "Shallow bear", "probability": 0.41},
        {"name": "Deep bear", "probability": 0.14},
    ],
    "binary_forecasts": [
        {"id": "F1", "statement": "Revenue reaches $1.6T in 2030", "probability": 0.62,
         "theme": "industry-tam", "confidence": 0.7},
        {"id": "F2", "statement": "TSMC keeps >=62% foundry share", "probability": 0.78},
        {"id": "F3", "statement": "HBM supply stays sold out through 2027",
         "probability": 0.55,
         "market_anchor": {"market_id": "HBM-27", "implied_yes_prob": 0.48,
                           "divergence": 0.07}},
    ],
}

ENSEMBLE = {
    "n_runs": 3,
    "agreement": 0.62,
    "scenarios": [
        {"name": "Supercycle holds", "mean_probability": 0.45, "stdev": 0.04,
         "min": 0.40, "max": 0.50, "support": 3, "support_ratio": 1.0,
         "key_drivers": ["AI revenue run-rate toward $400B", "CoWoS-L throughput"]},
        {"name": "Shallow bear", "mean_probability": 0.41, "stdev": 0.02,
         "min": 0.38, "max": 0.43, "support": 2, "support_ratio": 0.67,
         "key_drivers": ["Hyperscaler capex digestion", "AI revenue run-rate toward $400B"]},
        {"name": "Deep bear", "mean_probability": 0.14, "stdev": 0.05,
         "min": 0.08, "max": 0.20, "support": 1, "support_ratio": 0.33,
         "key_drivers": ["Credit-market shock", "Taiwan blockade tail"]},
    ],
}

ACTORS = {
    "actors": [
        {"name": "Samsung Electronics", "aliases": ["Samsung"], "influence": "high",
         "salience": {"tier": "high", "score": 0.95}, "role_class": "principal",
         "role": "Integrated memory + foundry"},
        {"name": "TSMC", "influence": "high", "salience": {"score": 0.98},
         "role_class": "principal", "role": "Pure-play foundry"},
        {"name": "BIS", "influence": "medium", "salience": "medium",
         "role_class": "arbiter", "role": "US export-control regulator"},
    ],
    "relationships": [
        # 'Samsung'（别名）与 'Samsung Electronics' 应合并为同一节点
        {"source": "Samsung", "target": "TSMC", "type": "COMPETES_WITH",
         "sign": "rival", "polarity": -0.9},
        {"source": "Samsung Electronics", "target": "TSMC", "type": "COMPETES_WITH",
         "sign": "rival", "polarity": -0.9},  # 归一后重复 → 去重
        {"source": "BIS", "target": "Samsung Electronics", "type": "REGULATES",
         "valence": "neutral", "polarity": 0.0},
        {"source": "TSMC", "target": "BIS", "type": "DEPENDS_ON",
         "sign": "ally", "polarity": 0.4},
    ],
}

TIMELINE = [
    {"date": "2026-01-13", "event": "BIS final rule tightens export controls >$1T scope"},
    {"date": "2026-01-13", "event": "BIS final rule tightens the export controls >$1T scope"},  # 近重 → 去重
    {"date": "2025-04-01", "event": "TSMC commits $165B Arizona capex expansion"},
    {"date": "2026-06", "event": "Taiwan strait military exercise raises risk premium"},
    {"date": "not-a-date", "event": "row without parseable date dropped"},
]

QUANTITATIVE = [
    {"metric": "Q1 2026 DRAM revenue", "value": "~100", "unit": "USD billion",
     "as_of_date": "2026-05-27", "tier": "S2", "is_stale": False, "source": "Counterpoint"},
    {"metric": "TSMC Q1 2026 gross margin", "value": "58", "unit": "%",
     "tier": "S1", "is_stale": False},
    {"metric": "Foundry 2024 market", "value": "155", "unit": "USD billion",
     "tier": "S1", "is_stale": True},
    {"metric": "HBM share SK hynix", "value": "52", "unit": "%",
     "tier": "S2", "is_stale": False},
    {"metric": "resolution date", "value": "2030-12-31", "unit": "date", "tier": "S1"},  # 排除
]

SOURCES = [
    {"url": "https://a", "source_origin": "fetched", "reachable": True, "tier": "S1"},
    {"url": "https://b", "source_origin": "fetched", "reachable": False, "tier": "S3"},
    {"url": "https://c", "source_origin": "cited", "reachable": True, "tier": "S2"},
    {"url": "https://d", "source_origin": "cited", "reachable": True, "tier": "S3"},
]

CONTESTED = [
    {"claim": "Is SMIC 5nm commercially viable?",
     "positions": [
         {"stance": "DUV multi-patterning closes the gap", "sources": ["a", "b"], "tier": "S2"},
         {"stance": "Yield 20-50% — not viable", "sources": ["c"], "tier": "S1"},
     ]},
    {"claim": "HBM4 pricing premium holds through 2027",
     "positions": [
         {"stance": "Sold out capacity → premium holds", "sources": ["d", "e", "f"], "tier": "S2"},
         {"stance": "Samsung re-entry compresses premium", "sources": ["g"], "tier": "S3"},
     ]},
]

GRAPH_PRIORS = {"TSMC": 0.9, "Samsung Electronics": 0.6, "CoWoS": 0.3}

WORLD_STATE = {
    "trajectory": [
        {"round": 0, "shares": {"Supercycle": 0.5, "Bear": 0.5}},
        {"round": 1, "shares": {"Supercycle": 0.6, "Bear": 0.4}},
    ],
}


def _price_history(market_id="HBM-27", n=5):
    t0 = 1_750_000_000
    return {market_id: [{"t": t0 + i * 86400, "p": 0.4 + 0.02 * i} for i in range(n)]}


def _full_artifacts():
    return {
        "forecast": json.loads(json.dumps(FORECAST)),
        "ensemble": json.loads(json.dumps(ENSEMBLE)),
        "actors": json.loads(json.dumps(ACTORS)),
        "timeline": json.loads(json.dumps(TIMELINE)),
        "quantitative": json.loads(json.dumps(QUANTITATIVE)),
        "sources": json.loads(json.dumps(SOURCES)),
        "contested": json.loads(json.dumps(CONTESTED)),
        "graph_priors": json.loads(json.dumps(GRAPH_PRIORS)),
        "world_state_trajectory": json.loads(json.dumps(WORLD_STATE)),
        "market_price_history": _price_history(),
    }


@pytest.fixture(autouse=True)
def _no_png_export(request, monkeypatch):
    """默认关闭 kaleido PNG 导出（Chromium 启动开销）；test_kaleido_* 命名的专项测试除外。"""
    if request.node.name.startswith("test_kaleido"):
        return
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)


needs_plotly = pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")


# ============================ build_all：manifest v2 + skipped ============================

@needs_plotly
def test_build_all_full_artifacts_produces_8_plus_charts(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("w9_full", str(tmp_path), _full_artifacts())
    ids = {e["id"] for e in items}
    expected = {"scenario_probabilities", "binary_forecast_dotplot", "model_vs_market",
                "timeline_lanes", "actor_network", "actor_influence_salience",
                "source_mix_sunburst", "quantitative_claims", "driver_tornado",
                "contested_claims", "worldstate_trajectory", "market_price_history_1"}
    assert expected <= ids
    assert len(items) >= 8
    for e in items:
        assert {"id", "path", "type", "title", "caption", "source",
                "placement_hint"} <= set(e.keys())
        assert os.path.exists(str(tmp_path / e["path"]))
    # manifest v2 落盘：items 一致 + skipped 是 {builder,reason} 列表
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert m["schema_version"] == 2
    assert m["items"] == items
    assert all(set(s.keys()) == {"builder", "reason"} for s in m["skipped"])


@needs_plotly
def test_build_all_skipped_reasons_no_silent_skips(tmp_path):
    # 只给 forecast → 其余构建器必须逐一记录 no_input（沉默跳过=缺陷）
    viz = ReportVisualizer()
    items = viz.build_all("w9_skips", str(tmp_path),
                          {"forecast": json.loads(json.dumps(FORECAST))})
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    reasons = {s["builder"]: s["reason"] for s in m["skipped"]}
    for builder in ("timeline_lanes", "actor_network", "actor_influence_salience",
                    "source_mix_sunburst", "quantitative_claims", "driver_tornado",
                    "contested_claims", "worldstate_trajectory", "market_price_history"):
        assert reasons.get(builder) == "no_input", f"{builder} 应记 no_input"
    assert any(e["id"] == "binary_forecast_dotplot" for e in items)


@needs_plotly
def test_build_all_logs_info_summary(tmp_path, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.services.report_visualizer"):
        ReportVisualizer().build_all("w9_log", str(tmp_path), _full_artifacts())
    assert any("报告可视化完成" in r.getMessage() for r in caplog.records)


def test_build_all_plotly_missing_matplotlib_fallback(tmp_path, monkeypatch):
    # plotly 缺失 → matplotlib 回退族产出 png 项；plotly 构建器全部记 skipped
    if not rv.MATPLOTLIB_AVAILABLE:
        pytest.skip("matplotlib not installed")
    monkeypatch.setattr(rv, "PLOTLY_AVAILABLE", False)
    viz = ReportVisualizer()
    items = viz.build_all("w9_noplotly", str(tmp_path), _full_artifacts())
    assert items and all(e["type"] == "png" for e in items)
    assert any(e["id"] == "scenario_probabilities" for e in items)
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert any(s["reason"] == "plotly_unavailable_or_disabled" for s in m["skipped"])


@needs_plotly
def test_build_all_kaleido_off_attaches_matplotlib_png(tmp_path):
    # PNG 导出关闭（autouse）→ 核心图（scenario/model_vs_market/worldstate/价格历史）由
    # matplotlib 回退补 PNG，挂到 html 项的 png_path 上（PDF/exec-brief 可嵌）
    if not rv.MATPLOTLIB_AVAILABLE:
        pytest.skip("matplotlib not installed")
    viz = ReportVisualizer()
    items = viz.build_all("w9_mplpng", str(tmp_path), _full_artifacts())
    by_id = {e["id"]: e for e in items}
    for item_id in ("scenario_probabilities", "model_vs_market",
                    "worldstate_trajectory", "market_price_history_1"):
        e = by_id[item_id]
        assert e["type"] == "html"
        assert e.get("png_path", "").endswith(".png"), f"{item_id} 缺 matplotlib 回退 PNG"
        with open(str(tmp_path / e["png_path"]), "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"


# ============================ 新构建器：单图行为 ============================

@needs_plotly
def test_scenario_bars_uses_ensemble_error_bands(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    rel = viz.build_scenario_bars_html(FORECAST, charts, ensemble=ENSEMBLE)
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    # ensemble min/max 误差带进入 figure 数据 JSON（'"arrayminus":[' 带值数组；裸词
    # 'arrayminus' 会命中内联 plotly.js 的 schema 源码，不能用作判据）
    assert '"arrayminus":[' in html
    assert "ensemble spread" in html
    # 无 ensemble、forecast 区间为 None → 无误差带但图仍产出。
    # 注意：构建器返回值恒为 'charts/<file>'（相对 report_dir 的约定路径），
    # 实际文件落在传入的 charts_dir 下，须按落盘位置读取。
    rel2 = viz.build_scenario_bars_html(FORECAST, str(tmp_path / "c2"))
    assert rel2 == os.path.join("charts", "scenario_probabilities.html")
    html2 = (tmp_path / "c2" / "scenario_probabilities.html").read_text(encoding="utf-8")
    assert '"arrayminus":[' not in html2


def test_scenario_rows_keep_canonical_taxonomy_and_only_match_uncertainty():
    """Unmatched ensemble taxonomies must never become extra bars in the published chart."""
    forecast = {
        "scenarios": [
            {"id": "base", "name": "Base case", "probability": 0.55},
            {"id": "bear", "name": "Bear case", "probability": 0.30},
            {"id": "tail", "name": "Tail risk", "probability": 0.15},
        ],
    }
    ensemble = {
        "scenarios": [
            {"name": "Base case", "mean_probability": 0.55,
             "probability": 0.22, "min": 0.50, "max": 0.60,
             "stdev": 0.05, "support_ratio": 1.0},
            {"name": "情景A：完全不同的分类", "mean_probability": 0.60,
             "probability": 0.48, "min": 0.55, "max": 0.65},
            {"name": "S2 — unrelated taxonomy", "mean_probability": 0.40,
             "probability": 0.30, "min": 0.35, "max": 0.45},
        ],
    }

    rows = rv._extract_scenario_rows(forecast, ensemble)

    assert {row["name"] for row in rows} == {"Base case", "Bear case", "Tail risk"}
    assert sum(row["p"] for row in rows) == pytest.approx(1.0)
    assert next(row for row in rows if row["name"] == "Base case")["stdev"] == 0.05
    assert next(row for row in rows if row["name"] == "Bear case")["stdev"] is None
    assert not any("different" in row["name"].lower() or "情景" in row["name"]
                   for row in rows)


def test_ensemble_only_scenario_rows_prefer_normalized_probability():
    ensemble = {
        "scenarios": [
            {"name": "A", "probability": 0.6, "mean_probability": 0.9},
            {"name": "B", "probability": 0.4, "mean_probability": 0.8},
        ],
    }
    rows = rv._extract_scenario_rows({}, ensemble)
    assert [row["p"] for row in rows] == [0.6, 0.4]
    assert sum(row["p"] for row in rows) == pytest.approx(1.0)


def test_ensemble_only_scenario_rows_reject_incoherent_diagnostic_means():
    ensemble = {
        "scenarios": [
            {"name": "Taxonomy A", "mean_probability": 0.8},
            {"name": "Unrelated taxonomy B", "mean_probability": 0.7},
        ],
    }
    assert rv._extract_scenario_rows({}, ensemble) == []


@needs_plotly
def test_binary_dotplot_market_markers_and_sorting(tmp_path):
    viz = ReportVisualizer()
    rel = viz.build_binary_dotplot_html(FORECAST, str(tmp_path / "charts"))
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert "Market implied" in html and "diamond" in html  # 锚点菱形
    assert "Confidence" in html                            # 置信度色标
    # 空/无概率 → None
    assert viz.build_binary_dotplot_html({"binary_forecasts": []}, str(tmp_path / "c2")) is None
    assert viz.build_binary_dotplot_html(
        {"binary_forecasts": [{"id": "X", "statement": "no prob"}]},
        str(tmp_path / "c3")) is None


@needs_plotly
def test_timeline_lanes_dedup_and_text_not_mangled(tmp_path):
    viz = ReportVisualizer()
    rel = viz.build_timeline_lanes_html(TIMELINE, str(tmp_path / "charts"))
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    # '>$1T' 原文进入 hover（HTML 转义为 &gt;），绝不再被 _san_label 改写成 ')$1T'
    assert "&gt;$1T" in html
    assert ")$1T" not in html
    # 同日近重事件去重：完整 hover 文案只出现一次（若未去重会有两份 '…&gt;$1T scope' hover；
    # 用 hover 专属长针避免误命中截断的可见短标签）
    assert html.count("&gt;$1T scope") == 1


@needs_plotly
def test_timeline_plot_uses_numbered_key_instead_of_inline_prose(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()

    def _capture(fig, _charts_dir, _stem, _item_id=None):
        captured["fig"] = fig
        return os.path.join("charts", "timeline_lanes.html")

    monkeypatch.setattr(viz, "_save_pair", _capture)
    dense = [
        {"date": f"2026-{month:02d}-15",
         "event": f"Company {month} announces a major capacity investment worth ${month}0B"}
        for month in range(1, 13)
    ]
    assert viz.build_timeline_lanes_html(dense, str(tmp_path / "charts"))

    fig = captured["fig"]
    visible_text = [str(text) for trace in fig.data for text in (trace.text or []) if text]
    assert visible_text
    assert all(re.fullmatch(r"\d{2}", text) for text in visible_text)
    annotation_text = [str(annotation.text) for annotation in fig.layout.annotations]
    assert annotation_text[0] == "<b>Key events</b>"
    assert any("capacity investment" in text for text in annotation_text[1:])
    assert fig.layout.showlegend is False


def test_prepare_timeline_events_cap_and_order(monkeypatch):
    monkeypatch.setattr(rv, "_cfg",
                        lambda name, default: 3 if name == "REPORT_VIZ_TIMELINE_MAX_EVENTS"
                        else default)
    rows = [{"date": f"2026-01-{d:02d}", "event": f"event {d} " + "x" * d}
            for d in range(1, 11)]
    events = rv._prepare_timeline_events(rows)
    assert len(events) == 3
    # 截断后仍按时间升序
    assert [e["dt"] for e in events] == sorted(e["dt"] for e in events)


@needs_plotly
def test_actor_network_normalizes_names_and_dedupes_edges(tmp_path):
    viz = ReportVisualizer()
    rel = viz.build_actor_network_html(ACTORS, str(tmp_path / "charts"),
                                       graph_priors=GRAPH_PRIORS)
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    # 'Samsung'（别名）被归一到 'Samsung Electronics'——图里不存在独立的 'Samsung' 节点标签
    assert "Samsung Electronics" in html
    # 关系符号图例
    assert "adversarial" in html and "supportive" in html
    # 纯助手级归一断言
    canon = rv._canonical_actor_map(ACTORS["actors"])
    assert rv._canonicalize("Samsung", canon) == "Samsung Electronics"
    assert rv._canonicalize("samsung electronics", canon) == "Samsung Electronics"
    assert rv._canonicalize("TSMC", canon) == "TSMC"
    assert rv._canonicalize("Unrelated Co", canon) == "Unrelated Co"


@needs_plotly
def test_actor_network_uses_collision_planned_annotations(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()

    def _capture(fig, _charts_dir, _stem, _item_id=None):
        captured["fig"] = fig
        return os.path.join("charts", "actor_network.html")

    monkeypatch.setattr(viz, "_save_pair", _capture)
    actors = {
        "actors": [
            {"name": f"Actor Organization {index:02d}", "role_class": "principal"}
            for index in range(18)
        ],
        "relationships": [
            {"source": f"Actor Organization {index:02d}",
             "target": f"Actor Organization {(index + 1) % 18:02d}",
             "type": "DEPENDS_ON", "sign": "ally"}
            for index in range(18)
        ],
    }
    assert viz.build_actor_network_html(actors, str(tmp_path / "charts"))

    fig = captured["fig"]
    node_traces = [trace for trace in fig.data if trace.hoverinfo == "text"]
    assert node_traces and all(trace.mode == "markers" for trace in node_traces)
    assert fig.layout.annotations
    assert all(annotation.axref == "x" and annotation.ayref == "y"
               for annotation in fig.layout.annotations)
    assert all(annotation.text.startswith("<b>Actor Organization")
               for annotation in fig.layout.annotations)


@needs_plotly
def test_actor_network_node_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rv, "_cfg",
                        lambda name, default: 4 if name == "REPORT_VIZ_NETWORK_MAX_NODES"
                        else default)
    rels = [{"source": f"S{i}", "target": f"T{i}", "type": "COMPETES_WITH",
             "sign": "rival"} for i in range(20)]
    viz = ReportVisualizer()
    rel = viz.build_actor_network_html({"relationships": rels}, str(tmp_path / "charts"))
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    # 上限 4 节点 → 至多 2 条完整边；S10 及以后的节点不可能出现
    assert "S10" not in html


@needs_plotly
def test_actor_bubble_sunburst_quant_tornado_contested_write_files(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    outs = {
        "bubble": viz.build_actor_bubble_html(ACTORS, charts),
        "sunburst": viz.build_source_sunburst_html(SOURCES, charts),
        "quant": viz.build_quantitative_dots_html(QUANTITATIVE, charts),
        "tornado": viz.build_driver_tornado_html(ENSEMBLE, charts,
                                                 graph_priors=GRAPH_PRIORS),
        "contested": viz.build_contested_dumbbell_html(CONTESTED, charts),
    }
    for name, rel in outs.items():
        assert rel is not None, f"{name} 未产出"
        assert rel.startswith("charts/") and rel.endswith(".html")
        assert os.path.exists(str(tmp_path / rel))


@needs_plotly
@pytest.mark.parametrize("builder,bad", [
    ("build_binary_dotplot_html", None),
    ("build_timeline_lanes_html", {}),
    ("build_actor_network_html", {"relationships": []}),
    ("build_actor_bubble_html", {"actors": []}),
    ("build_source_sunburst_html", []),
    ("build_quantitative_dots_html", [{"metric": "no value", "unit": "%"}]),
    ("build_driver_tornado_html", {"scenarios": []}),
    ("build_contested_dumbbell_html", [{"claim": "one position only",
                                        "positions": [{"stance": "solo"}]}]),
])
def test_new_builders_noop_on_missing_or_malformed(tmp_path, builder, bad):
    viz = ReportVisualizer()
    fn = getattr(viz, builder)
    assert fn(bad, str(tmp_path / "charts")) is None


@needs_plotly
def test_driver_tornado_merges_near_duplicate_drivers(tmp_path):
    # 两个情景引用同一驱动（词元近重）→ 合并为一条并累计权重
    viz = ReportVisualizer()
    rel = viz.build_driver_tornado_html(ENSEMBLE, str(tmp_path / "charts"))
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert html.count("AI revenue run-rate toward $400B") >= 1
    assert "cited by 2 scenarios" in html


# ============================ kaleido PNG 导出（专项，起一次 Chromium） ============================

@pytest.mark.skipif(not (rv.PLOTLY_AVAILABLE and rv.KALEIDO_AVAILABLE),
                    reason="plotly/kaleido not installed")
def test_kaleido_png_pair_standalone_builder(tmp_path):
    if not rv._KALEIDO_RUNTIME_OK:
        pytest.skip("kaleido runtime previously failed in this process")
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    rel = viz.build_scenario_bars_html(FORECAST, charts, ensemble=ENSEMBLE)
    assert rel is not None
    png_rel = viz._png_done.get("scenario_probabilities")
    assert png_rel == os.path.join("charts", "scenario_probabilities.png")
    with open(str(tmp_path / png_rel), "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


# ============================ 真实 handoff 工件集成冒烟（只读） ============================

_REAL_HANDOFF = os.path.join(os.path.dirname(__file__), "..", "uploads", "pipelines",
                             "pipe_f23527f7d903", "handoff")


@needs_plotly
@pytest.mark.skipif(not os.path.isdir(_REAL_HANDOFF),
                    reason="real handoff artifacts not present")
def test_build_all_real_handoff_integration(tmp_path):
    def _load(name):
        with open(os.path.join(_REAL_HANDOFF, name), "r", encoding="utf-8") as f:
            return json.load(f)
    arts = {
        "ensemble": _load("ensemble_forecast.json"),
        "actors": _load("actors.json"),
        "timeline": _load("timeline.json"),
        "quantitative": _load("quantitative.json"),
        "sources": _load("sources.json"),
        "contested": _load("contested.json"),
        "graph_priors": _load("graph_priors.json"),
    }
    viz = ReportVisualizer()
    items = viz.build_all("w9_real", str(tmp_path), arts)
    ids = {e["id"] for e in items}
    # 真实工件（无 forecast/worldstate/价格历史）也应产出 7 类图
    assert {"scenario_probabilities", "timeline_lanes", "actor_network",
            "actor_influence_salience", "source_mix_sunburst",
            "quantitative_claims", "driver_tornado", "contested_claims"} <= ids
    for e in items:
        assert os.path.exists(str(tmp_path / e["path"]))
