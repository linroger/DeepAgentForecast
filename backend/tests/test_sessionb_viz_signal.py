"""SESSION-B：报告图表信号密度回归——

1. 来源构成 sunburst（管线方法论自证/元数据图）默认不再占用报告槽位：build_all
   不产出该项，skipped 记 methodology_not_reader_facing；REPORT_META_CHARTS
   （Config 属性或同名环境变量）显式开启才恢复旧行为。构建器本体保持可调用。
2. 新增 metric_trajectories 构建器：把 quantitative.json 行按 (metric, unit)
   聚成 ≥2 时点的时间序列（成本曲线/部署量/渗透率——读者真正要的预测数据图），
   时点解析接受 YYYY / YYYY-MM / YYYY-MM-DD / 'by YYYY' / 'YYYY年'；单时点序列
   剔除；并以 id=metric_trajectories 注册进 build_all（排在 quantitative_claims
   之前）。
"""

import datetime as dt
import json
import os

import pytest

import app.services.report_visualizer as rv
from app.services.report_visualizer import (
    ReportVisualizer,
    _parse_metric_point_time,
    _prepare_metric_trajectories,
)

needs_plotly = pytest.mark.skipif(not rv.PLOTLY_AVAILABLE,
                                  reason="plotly not installed")


@pytest.fixture(autouse=True)
def _no_png_export(monkeypatch):
    """默认关闭 kaleido PNG 导出（Chromium 启动开销），与既有 viz 测试同款。"""
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)


SOURCES = [
    {"tier": "S1", "source_origin": "fetched", "reachable": True},
    {"tier": "S2", "source_origin": "cited", "reachable": False},
    {"tier": "S3", "source_origin": "fetched"},
]

# 两条 ≥2 时点的序列（混合日期格式）+ 单时点序列 + 不可解析行 + 非 dict 行。
QUANT_ROWS = [
    {"metric": "Battery pack cost", "unit": "USD per kWh", "value": "115",
     "as_of_date": "2025-01-15", "period_end": "2024-12-31",
     "definition": "Volume-weighted pack price",
     "source": "BNEF [S1]", "is_stale": False},
    {"metric": "Battery pack cost", "unit": "USD per kWh", "value": "99",
     "as_of_date": "2026-01-15", "period_end": "2025-12-31",
     "definition": "Volume-weighted pack price",
     "source": "BNEF [S1]", "is_stale": False},
    {"metric": "Battery pack cost", "unit": "USD per kWh", "value": "80",
     "as_of_date": "2026-01-15", "period_end": "2030-12-31",
     "definition": "Projected pack price", "source": "BNEF outlook [S1]",
     "source_url": "https://example.com/bnef-pack-outlook", "is_stale": False},
    {"metric": "Humanoid deployment", "unit": "units", "value": "1,200",
     "as_of_date": "2025-06-30", "definition": "Cumulative deployed fleet",
     "source": "Vendor filings [S8]", "is_stale": False},
    {"metric": "Humanoid deployment", "unit": "units", "value": "~5000",
     "as_of_date": "2025-07-15", "period_end": "2026-12-31",
     "definition": "Company deployment target", "source": "Vendor guidance [S8]",
     "source_url": "https://example.com/vendor-guidance", "is_stale": False},
    # 单时点序列 → 必须被剔除（两点起才算轨迹）
    {"metric": "Solo metric", "unit": "%", "value": "42",
     "as_of_date": "2025", "definition": "one point only", "source": "X"},
    # 不可解析：无数值 / 无时点 / 非 dict
    {"metric": "No value", "unit": "%", "value": "n/a", "as_of_date": "2025"},
    {"metric": "No date", "unit": "%", "value": "7", "as_of_date": "someday"},
    "not-a-dict",
]


# ============================ 时点解析（纯函数） ============================

def test_parse_metric_point_time_formats():
    assert _parse_metric_point_time({"as_of_date": "2025-12-31"}) == dt.date(2025, 12, 31)
    assert _parse_metric_point_time({"as_of_date": "2025-12"}) == dt.date(2025, 12, 1)
    assert _parse_metric_point_time({"as_of_date": "2024"}) == dt.date(2024, 1, 1)
    assert _parse_metric_point_time({"date": "by 2030"}) == dt.date(2030, 1, 1)
    assert _parse_metric_point_time({"period": "2035年"}) == dt.date(2035, 1, 1)
    assert _parse_metric_point_time({"period": {"end": "2026-06-30"}}) == dt.date(2026, 6, 30)
    # 不可解析 → None（不猜）
    assert _parse_metric_point_time({"as_of_date": "someday"}) is None
    assert _parse_metric_point_time({}) is None


def test_prepare_metric_trajectories_grouping_and_exclusions():
    series = _prepare_metric_trajectories(QUANT_ROWS)
    assert {s["metric"] for s in series} == {"Battery pack cost", "Humanoid deployment"}
    battery = next(s for s in series if s["metric"] == "Battery pack cost")
    # 时点升序：2024 → 2025-12 → by 2030；数值抽取容忍 '~' 与千分位逗号
    assert [p["value"] for p in battery["points"]] == [115.0, 99.0, 80.0]
    assert battery["points"][-1]["date"] == dt.date(2030, 12, 31)
    humanoid = next(s for s in series if s["metric"] == "Humanoid deployment")
    assert [p["value"] for p in humanoid["points"]] == [1200.0, 5000.0]
    # 畸形入参 → 空（degrade-safe）
    assert _prepare_metric_trajectories(None) == []
    assert _prepare_metric_trajectories({"rows": []}) == []


def test_prepare_metric_trajectories_merges_year_embedded_metric_names():
    """研究抽取常把时点写进指标名——'Tesla Optimus 2025 target' 与
    'Tesla Optimus 2026 target' 必须折叠成同一条部署轨迹（真实 handoff
    工件的主导形态；不折叠则该图对真实数据永远空转）。"""
    rows = [
        {"metric": "Tesla Optimus 2025 target", "unit": "units", "value": "5000",
         "as_of_date": "2025-01-15", "period_end": "2025-12-31",
         "definition": "deployment target", "source": "Vendor [S3]",
         "source_url": "https://example.com/optimus-2025"},
        {"metric": "Tesla Optimus 2026 target", "unit": "units", "value": "100000",
         "as_of_date": "2025-07-15", "period_end": "2026-12-31",
         "definition": "deployment target", "source": "Vendor [S3]",
         "source_url": "https://example.com/optimus-2026"},
    ]
    series = _prepare_metric_trajectories(rows)
    assert len(series) == 1
    assert len(series[0]["points"]) == 2
    assert series[0]["metric"] == "Tesla Optimus"
    # 逐点保留原始 metric 名供 hover 使用
    assert [p["metric"] for p in series[0]["points"]] == [
        "Tesla Optimus 2025 target", "Tesla Optimus 2026 target"]


# ============================ 新构建器（HTML 落盘） ============================

@needs_plotly
def test_build_metric_trajectories_html_mixed_dates(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    rel = viz.build_metric_trajectories_html(QUANT_ROWS, charts)
    assert rel is not None
    assert rel.startswith("charts/") and rel.endswith(".html")
    assert os.path.exists(str(tmp_path / rel))
    html = (tmp_path / rel).read_text(encoding="utf-8")
    assert "Key Metric Trajectories" in html
    assert "Battery pack cost" in html and "Humanoid deployment" in html
    # 单时点序列不得混入
    assert "Solo metric" not in html


@needs_plotly
def test_build_metric_trajectories_html_none_on_insufficient_data(tmp_path):
    viz = ReportVisualizer()
    charts = str(tmp_path / "charts")
    assert viz.build_metric_trajectories_html([], charts) is None
    assert viz.build_metric_trajectories_html(None, charts) is None
    # 全部单时点 → 无合格序列 → None
    solo = [{"metric": "M", "unit": "%", "value": "1", "as_of_date": "2025"}]
    assert viz.build_metric_trajectories_html(solo, charts) is None


# ============================ build_all：sunburst 降位 ============================

def test_build_all_demotes_source_sunburst_by_default(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_sunburst_off", str(tmp_path), {"sources": SOURCES})
    assert not any(e["id"] == "source_mix_sunburst" for e in items)
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    reasons = {s["builder"]: s["reason"] for s in m["skipped"]}
    assert reasons["source_mix_sunburst"] == "methodology_not_reader_facing"


@needs_plotly
def test_build_all_renders_sunburst_when_meta_charts_flag_on(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rv, "_cfg",
        lambda name, default: True if name == "REPORT_META_CHARTS" else default)
    viz = ReportVisualizer()
    items = viz.build_all("sb_sunburst_on", str(tmp_path), {"sources": SOURCES})
    by_id = {e["id"]: e for e in items}
    assert "source_mix_sunburst" in by_id
    assert os.path.exists(str(tmp_path / by_id["source_mix_sunburst"]["path"]))
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert all(s["builder"] != "source_mix_sunburst" for s in m["skipped"])


@needs_plotly
def test_build_all_renders_sunburst_when_meta_charts_env_on(tmp_path, monkeypatch):
    # Config 无 REPORT_META_CHARTS 键 → 回退读同名环境变量
    monkeypatch.setenv("REPORT_META_CHARTS", "1")
    viz = ReportVisualizer()
    items = viz.build_all("sb_sunburst_env", str(tmp_path), {"sources": SOURCES})
    assert any(e["id"] == "source_mix_sunburst" for e in items)


def test_build_source_sunburst_html_still_callable(tmp_path):
    # 降位 ≠ 删除：诊断 helper 本体保持可用（与 tornado/contested 同约定）
    if not rv.PLOTLY_AVAILABLE:
        pytest.skip("plotly not installed")
    viz = ReportVisualizer()
    rel = viz.build_source_sunburst_html(SOURCES, str(tmp_path / "charts"))
    assert rel is not None and os.path.exists(str(tmp_path / rel))


# ============================ build_all：metric_trajectories 注册 ============================

@needs_plotly
def test_build_all_registers_metric_trajectories(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_traj", str(tmp_path), {"quantitative": QUANT_ROWS})
    by_id = {e["id"]: e for e in items}
    entry = by_id.get("metric_trajectories")
    assert entry is not None
    assert entry["source"] == "quantitative"
    assert entry["placement_hint"] == "quantitative"
    assert entry["title"] == "Key Metric Trajectories (research-extracted)"
    assert os.path.exists(str(tmp_path / entry["path"]))
    # 注册顺序：metric_trajectories 排在 quantitative_claims 之前
    ids = [e["id"] for e in items]
    if "quantitative_claims" in ids:
        assert ids.index("metric_trajectories") < ids.index("quantitative_claims")
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert all(s["builder"] != "metric_trajectories" for s in m["skipped"])


@needs_plotly
def test_build_all_metric_trajectories_no_input_recorded(tmp_path):
    # 无 quantitative 工件 → 不沉默：skipped 记 no_input
    viz = ReportVisualizer()
    viz.build_all("sb_traj_skip", str(tmp_path), {"sources": SOURCES})
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    reasons = {s["builder"]: s["reason"] for s in m["skipped"]}
    assert reasons["metric_trajectories"] == "no_input"
