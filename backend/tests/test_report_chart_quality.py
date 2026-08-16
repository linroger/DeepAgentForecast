"""Chart-quality forensic-audit fixes (three gaps, red-first).

GAP 1 — uncertainty encodings were dead code: forecast.json computes
self_consistency_k / self_consistency_mean_spread but real scenarios shipped
without p_low/p_high (the red-team critique re-normalization strips them), so
every interval/error-bar path in the visualizer stayed dormant.  The extractor
now re-derives honest intervals from the pooled spread (never fabricating), and
the visualizer consumes them with visible provenance; bare point estimates say
so in the subtitle instead of silently rendering as certainty.

GAP 2 — near-empty full-page charts: model_vs_market and quantitative_claims now
carry minimum-data gates; below threshold the slot degrades to a compact
markdown table recorded in viz_manifest.json skipped[] with reason
'below_density_threshold' (+ fallback_path), and single market anchors keep
reaching readers through the binary dotplot's paired diamonds.

GAP 3 — PNG legibility: dotplot statements and timeline key labels word-wrap to
two lines instead of truncating mid-threshold; the timeline 'Date' axis title no
longer collides with the key-events block; beyond-plan dots get a '+N more'
note; quantitative panels get panel-distinct subtitles.

Offline, no LLM beyond the shared FakeLLMClient stub, no kaleido/Chromium.
"""

import os
import re

import pytest

from app.services import forecast_extractor as fe
from app.services import report_visualizer as rv
from app.services.report_visualizer import ReportVisualizer
from tests.conftest import FakeLLMClient

needs_plotly = pytest.mark.skipif(not rv.PLOTLY_AVAILABLE, reason="plotly not installed")


@pytest.fixture(autouse=True)
def _no_png_export(monkeypatch):
    """PNG 导出会拉起 Chromium；本套件只验证数据/布局，全部关闭。"""
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)


# ─────────────────────────────────────────────────────────────────────────────
# GAP 1(a) — extractor emits honest self-consistency intervals
# ─────────────────────────────────────────────────────────────────────────────

def _sc_forecast(k=5, spread=0.06, scenarios=None):
    return {
        "headline": "X",
        "scenarios": scenarios if scenarios is not None else [
            {"name": "Base case", "probability": 0.5,
             "resolution_criteria": "A>50% by 2030"},
            {"name": "Bear case", "probability": 0.3,
             "resolution_criteria": "A<30% by 2030"},
            {"name": "Status quo hold", "probability": 0.2,
             "resolution_criteria": "otherwise"},
        ],
        "confidence": "medium",
        "self_consistency_k": k,
        "self_consistency_mean_spread": spread,
    }


def test_apply_self_consistency_intervals_emits_clamped_bounds_with_provenance():
    out = fe.apply_self_consistency_intervals(_sc_forecast())
    base = out["scenarios"][0]
    assert base["p_low"] == pytest.approx(0.44)
    assert base["p_high"] == pytest.approx(0.56)
    assert base["interval_source"] == "self_consistency_spread"
    prov = out["interval_provenance"]
    assert prov == {"source": "self_consistency_spread", "k": 5, "spread": 0.06}
    # 全部情景都拿到区间
    assert all(s["p_low"] <= s["probability"] <= s["p_high"]
               for s in out["scenarios"])


def test_apply_self_consistency_intervals_clamps_to_unit_interval():
    fc = _sc_forecast(scenarios=[
        {"name": "Tail", "probability": 0.02, "resolution_criteria": "x"},
        {"name": "Peak", "probability": 0.98, "resolution_criteria": "y"},
    ])
    out = fe.apply_self_consistency_intervals(fc)
    assert out["scenarios"][0]["p_low"] == 0.0
    assert out["scenarios"][1]["p_high"] == 1.0


def test_apply_self_consistency_intervals_never_fabricates():
    # k < 2 → emit nothing
    out = fe.apply_self_consistency_intervals(_sc_forecast(k=1))
    assert "interval_provenance" not in out
    assert all("p_low" not in s for s in out["scenarios"])
    # spread missing → emit nothing
    fc = _sc_forecast()
    del fc["self_consistency_mean_spread"]
    out = fe.apply_self_consistency_intervals(fc)
    assert "interval_provenance" not in out
    assert all("p_low" not in s for s in out["scenarios"])
    # k missing entirely → emit nothing
    fc = _sc_forecast()
    del fc["self_consistency_k"]
    out = fe.apply_self_consistency_intervals(fc)
    assert "interval_provenance" not in out
    # 畸形输入 → 原样返回，不抛
    assert fe.apply_self_consistency_intervals(None) is None


def test_apply_self_consistency_intervals_preserves_existing_bounds():
    # 池化产生的逐情景 sd 区间（带 self_consistency_n）：保留数值、补溯源标记
    pooled = _sc_forecast(scenarios=[
        {"name": "Base", "probability": 0.5, "p_low": 0.42, "p_high": 0.58,
         "self_consistency_n": 4, "resolution_criteria": "x"},
        {"name": "Status quo hold", "probability": 0.5,
         "resolution_criteria": "y"},
    ])
    out = fe.apply_self_consistency_intervals(pooled)
    assert out["scenarios"][0]["p_low"] == 0.42
    assert out["scenarios"][0]["p_high"] == 0.58
    assert out["scenarios"][0]["interval_source"] == "self_consistency_spread"
    # 模型自行声明的区间（无 self_consistency_n）不冒充自一致性溯源
    declared = _sc_forecast(scenarios=[
        {"name": "Base", "probability": 0.5, "p_low": 0.40, "p_high": 0.60,
         "resolution_criteria": "x"},
        {"name": "Status quo hold", "probability": 0.5,
         "resolution_criteria": "y"},
    ])
    out2 = fe.apply_self_consistency_intervals(declared)
    assert out2["scenarios"][0]["p_low"] == 0.40
    assert "interval_source" not in out2["scenarios"][0]


def test_self_critique_restores_intervals_after_renormalization():
    """真实缺陷的最小重现：critique 经 _normalize_scenarios 重建情景并剥掉
    p_low/p_high，落盘的 forecast.json 只剩裸点估计。修复后区间从顶层
    self_consistency 数据确定性重建。"""
    fc = fe.apply_self_consistency_intervals(_sc_forecast(spread=0.08))
    fake = FakeLLMClient(json_responses=[{
        "scenarios": [
            {"name": "Base case", "probability": 0.45,
             "resolution_criteria": "A>50% by 2030"},
            {"name": "Bear case", "probability": 0.33,
             "resolution_criteria": "A<30% by 2030"},
            {"name": "Status quo hold", "probability": 0.22,
             "resolution_criteria": "otherwise"},
        ],
        "confidence": "medium",
    }])
    out = fe.self_critique_forecast(fc, fake)
    assert out.get("critiqued") is True
    for s in out["scenarios"]:
        p = s["probability"]
        assert s["p_low"] == pytest.approx(max(0.0, p - 0.08), abs=1e-4)
        assert s["p_high"] == pytest.approx(min(1.0, p + 0.08), abs=1e-4)
        assert s["interval_source"] == "self_consistency_spread"
    assert out["interval_provenance"]["k"] == 5


def test_premortem_rederives_interval_after_probability_shift(monkeypatch):
    monkeypatch.setattr(
        fe, "_cfg",
        lambda name, default: True if name == "REPORT_PREMORTEM" else default)
    fc = _sc_forecast(spread=0.06, scenarios=[
        {"name": "Base case", "probability": 0.6, "p_low": 0.58, "p_high": 0.62,
         "self_consistency_n": 5, "resolution_criteria": "x"},
        {"name": "Status quo hold", "probability": 0.4, "p_low": 0.38,
         "p_high": 0.42, "self_consistency_n": 5, "resolution_criteria": "y"},
    ])
    fake = FakeLLMClient(json_responses=[{
        "underweighted_scenario": "Status quo hold",
        "overconfident_scenario": "Base case",
        "missed_signals": ["ignored base rate"],
    }])
    out = fe.premortem_forecast(fc, fake)
    base = out["scenarios"][0]
    # 0.6 → 0.55 移出了旧区间 [0.58, 0.62] → 以 ±spread 诚实重建
    assert base["probability"] == pytest.approx(0.55)
    assert base["p_low"] == pytest.approx(0.49, abs=1e-4)
    assert base["p_high"] == pytest.approx(0.61, abs=1e-4)
    assert base["interval_source"] == "self_consistency_spread"


def test_derive_forecast_spine_pools_and_tags_interval_provenance(monkeypatch):
    monkeypatch.setattr(
        fe, "_cfg",
        lambda name, default: 3 if name == "REPORT_SPINE_SELFCONSISTENCY_K" else default)
    draw = {
        "headline": "X", "horizon": "2030",
        "scenarios": [
            {"name": "情景A", "probability": 0.6, "resolution_criteria": "A>50%"},
            {"name": "维持现状", "probability": 0.4, "resolution_criteria": "无变化"},
        ],
        "confidence": "medium",
    }
    draw2 = {**draw, "scenarios": [
        {"name": "情景A", "probability": 0.5, "resolution_criteria": "A>50%"},
        {"name": "维持现状", "probability": 0.5, "resolution_criteria": "无变化"},
    ]}
    draw3 = {**draw, "scenarios": [
        {"name": "情景A", "probability": 0.7, "resolution_criteria": "A>50%"},
        {"name": "维持现状", "probability": 0.3, "resolution_criteria": "无变化"},
    ]}
    fake = FakeLLMClient(json_responses=[draw, draw2, draw3])
    out = fe.derive_forecast_spine(fake, central_question="q", horizon="2030")
    assert out["self_consistency_k"] == 3
    assert out["interval_provenance"]["source"] == "self_consistency_spread"
    for s in out["scenarios"]:
        assert s["p_low"] <= s["probability"] <= s["p_high"]
        assert s["interval_source"] == "self_consistency_spread"


# ─────────────────────────────────────────────────────────────────────────────
# GAP 1(b) — visualizer consumes the intervals with visible provenance
# ─────────────────────────────────────────────────────────────────────────────

_SC_VIZ_FORECAST = {
    "scenarios": [
        {"name": "Base case", "probability": 0.5, "p_low": 0.44, "p_high": 0.56,
         "interval_source": "self_consistency_spread"},
        {"name": "Bear case", "probability": 0.3, "p_low": 0.24, "p_high": 0.36,
         "interval_source": "self_consistency_spread"},
        {"name": "Status quo", "probability": 0.2, "p_low": 0.14, "p_high": 0.26,
         "interval_source": "self_consistency_spread"},
    ],
    "interval_provenance": {"source": "self_consistency_spread", "k": 5,
                            "spread": 0.06},
    "self_consistency_k": 5,
    "self_consistency_mean_spread": 0.06,
}


def test_extract_scenario_rows_recognizes_self_consistency_source():
    rows = rv._extract_scenario_rows(_SC_VIZ_FORECAST)
    assert {r["interval_source"] for r in rows} == {"self_consistency"}
    assert {r["interval_label"] for r in rows} == {"Self-consistency spread"}


@needs_plotly
def test_scenario_bars_html_annotates_self_consistency_provenance(tmp_path):
    viz = ReportVisualizer()
    rel = viz.build_scenario_bars_html(_SC_VIZ_FORECAST, str(tmp_path / "charts"))
    assert rel is not None
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert '"arrayminus":[' in html                      # 误差棒真的画了
    assert "Self-consistency spread" in html             # 图例来源标签
    assert "self-consistency" in html and "k=5" in html  # 副标题溯源
    assert "(self-consistency spread)" in html           # 标题来源后缀
    assert "point estimates only" not in html


@needs_plotly
def test_scenario_bars_html_bare_points_say_so(tmp_path):
    viz = ReportVisualizer()
    forecast = {"scenarios": [
        {"name": "A", "probability": 0.6},
        {"name": "B", "probability": 0.4},
    ]}
    rel = viz.build_scenario_bars_html(forecast, str(tmp_path / "charts"))
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert "point estimates only" in html
    assert '"arrayminus":[' not in html


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_static_scenario_bars_render_self_consistency_intervals(tmp_path, monkeypatch):
    captured = {}
    original_save = ReportVisualizer._save

    def _spy(self, fig, charts_dir, filename):
        axis = fig.axes[0]
        legend = axis.get_legend()
        captured["title"] = axis.get_title()
        captured["legend"] = ([t.get_text() for t in legend.get_texts()]
                              if legend else [])
        return original_save(self, fig, charts_dir, filename)

    monkeypatch.setattr(ReportVisualizer, "_save", _spy)
    viz = ReportVisualizer()
    assert viz.build_scenario_bars(_SC_VIZ_FORECAST, str(tmp_path / "charts")) is not None
    assert captured["title"].endswith("(self-consistency spread)")
    assert captured["legend"] == ["Self-consistency spread"]


@needs_plotly
def test_binary_dotplot_subtitle_states_missing_confidence(tmp_path):
    viz = ReportVisualizer()
    forecast = {"binary_forecasts": [
        {"id": "F1", "statement": "A happens", "probability": 0.7},
        {"id": "F2", "statement": "B happens", "probability": 0.4},
    ]}
    rel = viz.build_binary_dotplot_html(forecast, str(tmp_path / "charts"))
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert "confidence not declared" in html
    assert "Declared confidence" not in html
    # 全量声明 confidence 时旧行为保留、且不再声称缺数据
    declared = {"binary_forecasts": [
        {"id": "F1", "statement": "A happens", "probability": 0.7, "confidence": 0.8},
        {"id": "F2", "statement": "B happens", "probability": 0.4, "confidence": 0.6},
    ]}
    rel2 = viz.build_binary_dotplot_html(declared, str(tmp_path / "c2"))
    html2 = (tmp_path / "c2" / "binary_forecast_dotplot.html").read_text(encoding="utf-8")
    assert rel2 is not None
    assert "Declared confidence" in html2
    assert "confidence not declared" not in html2


# ─────────────────────────────────────────────────────────────────────────────
# GAP 2 — minimum-data gates with markdown-table degradation
# ─────────────────────────────────────────────────────────────────────────────

def _anchored_forecast(n_anchored, n_plain=1):
    bfs = []
    for i in range(1, n_anchored + 1):
        bfs.append({
            "id": f"F{i}",
            "statement": f"Proposition {i} resolves yes by 2030",
            "probability": 0.3 + 0.1 * i,
            "market_anchor": {"market_id": f"MKT-{i}",
                              "implied_yes_prob": 0.25 + 0.1 * i},
        })
    for j in range(n_plain):
        bfs.append({"id": f"P{j}", "statement": f"Unanchored proposition {j}",
                    "probability": 0.5})
    return {"binary_forecasts": bfs}


@needs_plotly
def test_model_vs_market_below_three_anchors_degrades_to_table(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.build_model_vs_market_html(_anchored_forecast(1), charts) is None
    table = tmp_path / "charts" / "model_vs_market_table.md"
    assert table.exists()
    text = table.read_text(encoding="utf-8")
    assert "Proposition 1 resolves yes by 2030" in text
    assert "40" in text and "35" in text  # model 0.40 / market 0.35（百分比呈现）
    note = viz._skip_notes.get("model_vs_market")
    assert note == {"reason": "below_density_threshold",
                    "fallback_path": os.path.join("charts", "model_vs_market_table.md")}


@needs_plotly
def test_model_vs_market_renders_at_three_anchors(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    rel = viz.build_model_vs_market_html(_anchored_forecast(3), charts)
    assert rel is not None and rel.endswith(".html")
    assert "model_vs_market" not in viz._skip_notes


@pytest.mark.skipif(not rv.MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
def test_static_model_vs_market_applies_same_density_gate(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.build_model_vs_market(_anchored_forecast(2), charts) is None
    assert viz.build_model_vs_market(_anchored_forecast(3), charts) is not None


@needs_plotly
def test_build_all_records_density_skip_and_folds_anchor_into_dotplot(tmp_path):
    import json
    viz = ReportVisualizer()
    items = viz.build_all("gap2", str(tmp_path),
                          {"forecast": _anchored_forecast(1, n_plain=3)})
    ids = {e["id"] for e in items}
    assert "model_vs_market" not in ids
    assert "binary_forecast_dotplot" in ids
    manifest = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    entry = next(s for s in manifest["skipped"] if s["builder"] == "model_vs_market")
    assert entry["reason"] == "below_density_threshold"
    assert entry["fallback_path"] == os.path.join("charts", "model_vs_market_table.md")
    assert (tmp_path / entry["fallback_path"]).exists()
    # 单市场锚点折叠进点阵图的成对菱形（读者不失去市场对照）
    dot = next(e for e in items if e["id"] == "binary_forecast_dotplot")
    html = (tmp_path / dot["path"]).read_text(encoding="utf-8")
    assert "Market implied" in html and "diamond" in html


def _thin_quant_rows():
    """同一指标、同一天、两个来源 → 单面板 n=2 且仅 1 个类目：低于密度门。"""
    return [
        {"metric": "Utility-scale solar capex", "value": "850", "unit": "USD per kW",
         "as_of_date": "2026-05-01", "source": "IEA WEO",
         "definition": "Installed capex per kW for utility-scale solar", "tier": "S1"},
        {"metric": "Utility-scale solar capex", "value": "910", "unit": "USD per kW",
         "as_of_date": "2026-05-01", "source": "BNEF outlook",
         "definition": "Installed capex per kW for utility-scale solar", "tier": "S2"},
    ]


@needs_plotly
def test_quantitative_thin_panel_degrades_to_table(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.build_quantitative_dots_html(_thin_quant_rows(), charts) is None
    table = tmp_path / "charts" / "quantitative_claims_table.md"
    assert table.exists()
    text = table.read_text(encoding="utf-8")
    assert "Utility-scale solar capex" in text
    assert "850" in text and "910" in text and "USD per kW" in text
    assert viz._skip_notes["quantitative_claims"]["reason"] == "below_density_threshold"


@needs_plotly
def test_quantitative_two_distinct_metrics_still_render(tmp_path):
    rows = [
        {"metric": "TSMC Q1 2026 gross margin", "value": "58", "unit": "% gross margin",
         "as_of_date": "2026-03-31", "source": "TSMC Q1 results", "tier": "S1",
         "definition": "Quarterly gross margin as percentage of revenue"},
        {"metric": "Samsung Q1 2026 gross margin", "value": "41", "unit": "% gross margin",
         "as_of_date": "2026-03-31", "source": "Samsung Q1 results", "tier": "S2",
         "definition": "Quarterly gross margin as percentage of revenue"},
    ]
    viz = ReportVisualizer()
    rel = viz.build_quantitative_dots_html(rows, str(tmp_path / "charts"))
    assert rel is not None
    assert "quantitative_claims" not in viz._skip_notes


@needs_plotly
def test_build_all_quantitative_density_skip_reason(tmp_path):
    import json
    viz = ReportVisualizer()
    viz.build_all("gap2q", str(tmp_path), {"quantitative": _thin_quant_rows()})
    manifest = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    entry = next(s for s in manifest["skipped"] if s["builder"] == "quantitative_claims")
    assert entry["reason"] == "below_density_threshold"
    assert (tmp_path / entry["fallback_path"]).exists()


# ─────────────────────────────────────────────────────────────────────────────
# GAP 3 — PNG legibility: wrapping, axis-title collision, +N more, subtitles
# ─────────────────────────────────────────────────────────────────────────────

def test_wrap_label_wraps_at_word_boundaries_to_two_lines():
    text = ("The US effective tariff rate on imports averages over 10% from "
            "2026 through 2028 according to the dossier")
    out = rv._wrap_label(text, width=55, max_lines=2)
    lines = out.split("<br>")
    assert len(lines) == 2
    assert all(len(line) <= 55 for line in lines)
    assert "…" not in out                      # 110 字符预算内不截断
    assert " ".join(lines) == re.sub(r"\s+", " ", text)


def test_wrap_label_truncates_only_past_budget_and_handles_cjk():
    long = "word " * 60
    out = rv._wrap_label(long, width=55, max_lines=2)
    assert out.endswith("…")
    assert len(out.split("<br>")) == 2
    # 无空格的 CJK 长串按宽度硬切行，不整串溢出
    cjk = "中" * 80
    out2 = rv._wrap_label(cjk, width=20, max_lines=2)
    lines = out2.split("<br>")
    assert len(lines) == 2 and all(len(line) <= 21 for line in lines)
    # 短文本原样直通
    assert rv._wrap_label("short", width=55, max_lines=2) == "short"
    assert rv._wrap_label(None, fallback="fb") == "fb"


@needs_plotly
def test_binary_dotplot_wraps_long_statements_instead_of_truncating(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()
    monkeypatch.setattr(
        viz, "_save_pair",
        lambda fig, *a, **k: captured.setdefault("fig", fig)
        or os.path.join("charts", "binary_forecast_dotplot.html"))
    stmt = ("The US effective tariff rate on imports averages over 10% from "
            "2026 through 2028 per the trade dossier")
    forecast = {"binary_forecasts": [
        {"id": "F1", "statement": stmt, "probability": 0.62},
        {"id": "F2", "statement": "Short one", "probability": 0.4},
    ]}
    assert viz.build_binary_dotplot_html(forecast, str(tmp_path / "charts"))
    fig = captured["fig"]
    labels = [str(y) for trace in fig.data for y in (trace.y or [])]
    wrapped = [lab for lab in labels if "<br>" in lab]
    assert wrapped, "long statements must wrap to two lines for PNG export"
    assert all("…" not in lab for lab in wrapped)
    assert any("2026 through 2028" in lab.replace("<br>", " ") for lab in wrapped)


@needs_plotly
def test_timeline_key_wraps_suppresses_date_title_and_notes_overflow(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()
    monkeypatch.setattr(
        viz, "_save_pair",
        lambda fig, *a, **k: captured.setdefault("fig", fig)
        or os.path.join("charts", "timeline_lanes.html"))
    dense = [
        {"date": f"2026-{month:02d}-15",
         "event": (f"Company {month} announces a major multi-year capacity "
                   f"investment worth ${month}0B across two continents with "
                   "phased regulatory approval milestones")}
        for month in range(1, 13)
    ]
    assert viz.build_timeline_lanes_html(dense, str(tmp_path / "charts"))
    fig = captured["fig"]
    texts = [str(a.text) for a in fig.layout.annotations]
    key_entries = [t for t in texts if re.match(r"<b>\d{2}</b>", t)]
    assert key_entries
    assert any("<br>" in t for t in key_entries), "key labels must wrap, not cut at 52"
    assert all("…" not in t or len(re.sub(r"<[^>]+>", "", t)) > 100 for t in key_entries)
    # key 区在场 → 'Date' 轴标题让位（PNG 中两者碰撞）
    assert not fig.layout.xaxis.title.text
    # 计划外事件不再是匿名圆点：给出 +N more 说明
    labeled = len(key_entries)
    assert labeled < len(dense)
    assert any(re.search(r"\+\d+ more", t) for t in texts)


@needs_plotly
def test_timeline_without_overflow_has_no_more_note(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()
    monkeypatch.setattr(
        viz, "_save_pair",
        lambda fig, *a, **k: captured.setdefault("fig", fig)
        or os.path.join("charts", "timeline_lanes.html"))
    few = [{"date": f"2026-0{d}-01", "event": f"Compact event {d}"}
           for d in range(1, 5)]
    assert viz.build_timeline_lanes_html(few, str(tmp_path / "charts"))
    texts = [str(a.text) for a in captured["fig"].layout.annotations]
    assert not any(re.search(r"\+\d+ more", t) for t in texts)


def test_quant_panel_subtitles_are_panel_distinct():
    panels = [
        {"unit": "USD/kW", "as_of": "2026-05-01", "time_basis": "as-of",
         "rows": [{"metric": "Solar capex"}, {"metric": "Solar capex"}]},
        {"unit": "USD/kW", "as_of": "2026-05-01", "time_basis": "annual",
         "rows": [{"metric": "Wind capex"}, {"metric": "Wind capex"}]},
    ]
    titles = rv._quant_panel_subtitles(panels)
    assert len(titles) == len(set(titles)), f"identical panel titles: {titles}"
    assert all("(n=2)" in t for t in titles)


@needs_plotly
def test_quantitative_panels_get_distinct_subplot_titles(tmp_path, monkeypatch):
    captured = {}
    viz = ReportVisualizer()
    monkeypatch.setattr(
        viz, "_save_pair",
        lambda fig, *a, **k: captured.setdefault("fig", fig)
        or os.path.join("charts", "quantitative_claims.html"))
    rows = [
        # 面板 1：% gross margin（两个不同指标）
        {"metric": "TSMC Q1 2026 gross margin", "value": "58", "unit": "% gross margin",
         "as_of_date": "2026-03-31", "source": "TSMC Q1 results", "tier": "S1",
         "definition": "Quarterly gross margin as percentage of revenue"},
        {"metric": "Samsung Q1 2026 gross margin", "value": "41", "unit": "% gross margin",
         "as_of_date": "2026-03-31", "source": "Samsung Q1 results", "tier": "S2",
         "definition": "Quarterly gross margin as percentage of revenue"},
        # 面板 2：同 glyph 单位、不同 as-of（此前两个面板同名的复现输入）
        {"metric": "SK hynix Q2 2026 gross margin", "value": "54", "unit": "% gross margin",
         "as_of_date": "2026-06-30", "source": "SK hynix Q2 results", "tier": "S1",
         "definition": "Quarterly gross margin as percentage of revenue"},
        {"metric": "Micron Q2 2026 gross margin", "value": "38", "unit": "% gross margin",
         "as_of_date": "2026-06-30", "source": "Micron Q2 results", "tier": "S2",
         "definition": "Quarterly gross margin as percentage of revenue"},
    ]
    assert viz.build_quantitative_dots_html(rows, str(tmp_path / "charts"))
    fig = captured["fig"]
    subplot_titles = [str(a.text) for a in fig.layout.annotations]
    assert len(subplot_titles) == 2
    assert len(set(subplot_titles)) == 2, f"identical subplot titles: {subplot_titles}"
