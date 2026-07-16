"""SESSION-B：预测「数据」图回归（DRF2 定量 schema 新字段驱动）——

报告默认图集必须是「预测数据优先」：成本曲线 / 部署轨迹 / 技术份额 / 区域对比，
而非管线自证/关系结构图。本套覆盖：

  (a) 带 metric_family + region + year + value_num 的行（两区域、多年份）→ build_all
      manifest 同时含 metric_trajectories 与 regional_comparison。
  (b) 带 technology 字段（≥2 技术）→ technology_shares 出现。
  (c) actor_network 已从默认槽位降为 opt-in：默认不在 manifest、skipped 记
      methodology_not_reader_facing；REPORT_META_CHARTS 显式开启才恢复（builder 仍可调用）。
  (d) 老数据（无新字段）仍经折叠名回退产出 metric_trajectories。
  (e) 真实 handoff 工件：新字段落地后按家族分组；未落地则回退路径 degrade-safe，并用
      另一份真实老工件证明回退路径确能产出 ≥1 序列。

纯函数 preparer 测试不依赖 plotly；落盘/build_all 测试用 needs_plotly 跳过守卫，并沿用既有
viz 测试的「关闭 kaleido PNG 导出」夹具（省去 Chromium 启动开销）。
"""

import json
import os

import pytest

import app.services.report_visualizer as rv
from app.services.report_visualizer import (
    ReportVisualizer,
    _metric_row_kind,
    _metric_row_is_publishable,
    _metric_row_region,
    _metric_row_value,
    _metric_row_year,
    _parse_metric_point_time,
    _metric_trajectory_panels,
    _prepare_metric_family_trajectories,
    _prepare_regional_comparison,
    _prepare_technology_shares,
)

needs_plotly = pytest.mark.skipif(not rv.PLOTLY_AVAILABLE,
                                  reason="plotly not installed")

_REAL_HANDOFF_ENRICHED = os.path.join(
    os.path.dirname(__file__), "..", "uploads", "pipelines",
    "pipe_bef6879b2e94", "handoff", "quantitative.json")
# 较旧的真实工件：指标名内嵌年份，折叠名回退能产出 ≥1 序列（验证回退路径）。
_REAL_HANDOFF_LEGACY = os.path.join(
    os.path.dirname(__file__), "..", "uploads", "pipelines",
    "pipe_f23527f7d903", "handoff", "quantitative.json")


@pytest.fixture(autouse=True)
def _no_png_export(monkeypatch):
    """默认关闭 kaleido PNG 导出（Chromium 启动开销），与既有 viz 测试同款。"""
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)


# 两区域（Global/China）× 多年份（2025 观测 / 2030 预测），带全套 DRF2 新字段。
REGIONAL_ROWS = [
    {"metric": "Global humanoid shipments 2025", "metric_family": "humanoid shipments",
     "unit": "units", "region": "Global", "year": 2025, "value_num": 13317,
     "value": "13317", "value_kind": "actual", "source": "Omdia [S1]",
     "definition": "units shipped worldwide", "as_of_date": "2026-01-15"},
    {"metric": "China humanoid shipments 2025", "metric_family": "humanoid shipments",
     "unit": "units", "region": "China", "year": 2025, "value_num": 5000,
     "value": "5000", "value_kind": "actual", "source": "AgiBot [S2]",
     "definition": "units shipped in China", "as_of_date": "2026-01-15"},
    {"metric": "Global humanoid shipments 2030 forecast",
     "metric_family": "humanoid shipments", "unit": "units", "region": "Global",
     "year": 2030, "value_num": 250000, "value": "250000", "value_kind": "forecast",
     "source": "Goldman Sachs [S2]", "definition": "forecast units worldwide",
     "as_of_date": "2026-01-15", "source_url": "https://example.com/gs-forecast"},
    {"metric": "China humanoid shipments 2030 forecast",
     "metric_family": "humanoid shipments", "unit": "units", "region": "China",
     "year": 2030, "value_num": 120000, "value": "120000", "value_kind": "forecast",
     "source": "Morgan Stanley [S2]", "definition": "forecast units in China",
     "as_of_date": "2026-01-15", "source_url": "https://example.com/ms-forecast"},
]

# 两技术（LFP/NMC）份额，同一 metric_family/year。
TECHNOLOGY_ROWS = [
    {"metric": "LFP pack share 2025", "metric_family": "battery chemistry mix",
     "unit": "% of pack GWh", "technology": "LFP", "region": "Global", "year": 2025,
     "value_num": 62, "value": "62", "value_kind": "actual", "source": "BNEF [S1]",
     "definition": "share of global pack GWh", "as_of_date": "2026-01-15"},
    {"metric": "NMC pack share 2025", "metric_family": "battery chemistry mix",
     "unit": "% of pack GWh", "technology": "NMC", "region": "Global", "year": 2025,
     "value_num": 33, "value": "33", "value_kind": "actual", "source": "BNEF [S1]",
     "definition": "share of global pack GWh", "as_of_date": "2026-01-15"},
    {"metric": "Sodium-ion pack share 2025", "metric_family": "battery chemistry mix",
     "unit": "% of pack GWh", "technology": "Sodium-ion", "region": "Global",
     "year": 2025, "value_num": 5, "value": "5", "value_kind": "actual",
     "source": "BNEF [S1]", "definition": "share of global pack GWh",
     "as_of_date": "2026-01-15"},
]

# 老数据：无任何新字段，但指标名内嵌年份 → 折叠名回退成一条轨迹（两点）。
LEGACY_ROWS = [
    {"metric": "Tesla Optimus 2025 target", "unit": "units", "value": "5000",
     "as_of_date": "2025-01-15", "period_end": "2025-12-31",
     "definition": "deployment target", "source": "Vendor [S3]",
     "source_url": "https://example.com/vendor-2025"},
    {"metric": "Tesla Optimus 2026 target", "unit": "units", "value": "100000",
     "as_of_date": "2025-07-15", "period_end": "2026-12-31",
     "definition": "deployment target", "source": "Vendor [S3]",
     "source_url": "https://example.com/vendor-2026"},
]

ACTORS = {
    "actors": [
        {"name": "Tesla", "influence": "high", "role_class": "vendor"},
        {"name": "AgiBot", "influence": "medium", "role_class": "vendor"},
    ],
    "relationships": [
        {"source": "Tesla", "target": "AgiBot", "type": "COMPETES", "sign": "-"},
    ],
}


# ============================ 纯函数 preparer ============================

def test_metric_row_field_readers_prefer_new_then_legacy():
    # region: region 优先，回退 geography
    assert _metric_row_region({"region": "China"}) == "China"
    assert _metric_row_region({"geography": "Global"}) == "Global"
    assert _metric_row_region({}) is None
    # year: 显式 int 优先，回退 period_end 的四位年（目标年而非发布日）
    assert _metric_row_year({"year": 2030}) == 2030
    assert _metric_row_year({"period_end": "2030-12-31", "as_of_date": "2026-01-01"}) == 2030
    nested = {"period": {"end": "2035-12-31"}, "as_of_date": "2026-01-01"}
    assert _metric_row_year(nested) == 2035
    assert _parse_metric_point_time(nested).isoformat() == "2035-12-31"
    assert _metric_row_year({"as_of_date": "2026-05-27"}) == 2026
    assert _metric_row_year({}) is None
    # value: value_num 优先，回退 value 文本解析
    assert _metric_row_value({"value_num": 42.0}) == 42.0
    assert _metric_row_value({"value_num": 999.0, "value": "40-60"}) == 50.0
    assert _metric_row_value({"value": "1,200"}) == 1200.0
    assert _metric_row_value({"value": "n/a"}) is None
    # kind: value_kind → value_type → 语义推断
    assert _metric_row_kind({"value_kind": "forecast"}) == "forecast"
    assert _metric_row_kind({"value_type": "observed"}) == "actual"
    assert _metric_row_kind({"metric": "2030 projection"}) == "forecast"


def test_report_visualizer_forecast_rows_require_external_provenance():
    base = {
        "metric": "2030 deployment forecast",
        "value_kind": "forecast",
        "source": "Published outlook",
        "definition": "forecast deployment in 2030",
        "as_of_date": "2026-01-15",
    }
    assert not _metric_row_is_publishable(base)
    assert not _metric_row_is_publishable({
        **base, "source_url": "file:///tmp/dossier.md",
    })
    assert not _metric_row_is_publishable({
        **base,
        "source_url": "https://example.com/internal",
        "definition": "analyst interpolation from the dossier",
    })
    assert _metric_row_is_publishable({
        **base, "source_url": "https://example.com/published-outlook",
    })
    assert _metric_row_is_publishable({
        **base,
        "value_kind": "actual",
        "definition": "observed deployment in 2025",
    })


def test_unproven_forecast_is_excluded_from_family_chart():
    rows = [dict(row) for row in REGIONAL_ROWS]
    for row in rows:
        if row["value_kind"] == "forecast":
            row.pop("source_url", None)
    assert _prepare_metric_family_trajectories(rows) == []


def test_prepare_metric_family_trajectories_groups_by_region_over_year():
    panels = _prepare_metric_family_trajectories(REGIONAL_ROWS)
    assert len(panels) == 1
    panel = panels[0]
    assert panel["split"] == "region"
    names = {ln["name"] for ln in panel["lines"]}
    assert names == {"Global", "China"}
    glob = next(ln for ln in panel["lines"] if ln["name"] == "Global")
    assert [p["x_label"] for p in glob["points"]] == ["2025", "2030"]
    assert [p["kind"] for p in glob["points"]] == ["actual", "forecast"]
    # 畸形入参 → 空（degrade-safe）
    assert _prepare_metric_family_trajectories(None) == []
    assert _prepare_metric_family_trajectories([{"metric_family": "x"}]) == []


def test_prepare_regional_comparison_latest_year_two_regions():
    panels = _prepare_regional_comparison(REGIONAL_ROWS)
    assert len(panels) == 1
    panel = panels[0]
    assert panel["year"] == 2030  # 最新且 ≥2 区域的年份
    regions = {r["region"]: r for r in panel["regions"]}
    assert set(regions) == {"Global", "China"}
    assert regions["Global"]["value"] == 250000.0
    assert regions["China"]["kind"] == "forecast"
    # 单区域 → 跳过
    solo = [dict(r) for r in REGIONAL_ROWS if r["region"] == "Global"]
    assert _prepare_regional_comparison(solo) == []


def test_prepare_technology_shares_two_techs():
    panels = _prepare_technology_shares(TECHNOLOGY_ROWS)
    assert len(panels) == 1
    panel = panels[0]
    techs = {t["tech"]: t for t in panel["techs"]}
    assert set(techs) == {"LFP", "NMC", "Sodium-ion"}
    # 占比归一（总和≈1）
    assert abs(sum(t["share"] for t in panel["techs"]) - 1.0) < 1e-9
    assert techs["LFP"]["value"] == 62.0
    # <2 技术 → 跳过（人形机器人跑 technology 为空的情形）
    assert _prepare_technology_shares(REGIONAL_ROWS) == []


def test_metric_trajectory_panels_prefers_family_then_legacy():
    # 带 metric_family → 家族路径
    assert _metric_trajectory_panels(REGIONAL_ROWS)[0]["split"] == "region"
    # 无 metric_family → 折叠名回退
    legacy = _metric_trajectory_panels(LEGACY_ROWS)
    assert len(legacy) == 1
    assert legacy[0]["split"] is None
    assert [p["x_label"] for p in legacy[0]["lines"][0]["points"]] == \
        ["2025-12-31", "2026-12-31"]


# ============================ (a) build_all：轨迹 + 区域对比 ============================

@needs_plotly
def test_build_all_regional_rows_yield_trajectories_and_regional(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_reg", str(tmp_path), {"quantitative": REGIONAL_ROWS})
    ids = {e["id"] for e in items}
    assert "metric_trajectories" in ids
    assert "regional_comparison" in ids
    by_id = {e["id"]: e for e in items}
    assert by_id["regional_comparison"]["placement_hint"] == "quantitative"
    assert os.path.exists(str(tmp_path / by_id["regional_comparison"]["path"]))
    assert os.path.exists(str(tmp_path / by_id["metric_trajectories"]["path"]))
    # 无 technology → technology_shares 不出图但记 empty_after_parse（不沉默）
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    reasons = {s["builder"]: s["reason"] for s in m["skipped"]}
    assert reasons.get("technology_shares") == "empty_after_parse"


# ============================ (b) build_all：技术份额 ============================

@needs_plotly
def test_build_all_technology_rows_yield_technology_shares(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_tech", str(tmp_path), {"quantitative": TECHNOLOGY_ROWS})
    by_id = {e["id"]: e for e in items}
    assert "technology_shares" in by_id
    assert by_id["technology_shares"]["placement_hint"] == "quantitative"
    assert os.path.exists(str(tmp_path / by_id["technology_shares"]["path"]))


# ============================ (c) actor_network 降为 opt-in ============================

def test_build_all_demotes_actor_network_by_default(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_actor_off", str(tmp_path), {"actors": ACTORS})
    assert not any(e["id"] == "actor_network" for e in items)
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    reasons = {s["builder"]: s["reason"] for s in m["skipped"]}
    assert reasons["actor_network"] == "methodology_not_reader_facing"


@needs_plotly
def test_build_all_renders_actor_network_when_meta_charts_on(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_META_CHARTS", "1")
    viz = ReportVisualizer()
    items = viz.build_all("sb_actor_on", str(tmp_path), {"actors": ACTORS})
    by_id = {e["id"]: e for e in items}
    assert "actor_network" in by_id
    assert os.path.exists(str(tmp_path / by_id["actor_network"]["path"]))
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert all(s["builder"] != "actor_network" for s in m["skipped"])


def test_build_actor_network_html_still_callable(tmp_path):
    # 降位 ≠ 删除：builder 本体保持可用（与 sunburst/tornado 同约定）
    if not rv.PLOTLY_AVAILABLE:
        pytest.skip("plotly not installed")
    viz = ReportVisualizer()
    rel = viz.build_actor_network_html(ACTORS, str(tmp_path / "charts"))
    assert rel is not None and os.path.exists(str(tmp_path / rel))


# ============================ (d) 老数据回退仍出轨迹 ============================

@needs_plotly
def test_build_all_legacy_rows_still_yield_metric_trajectories(tmp_path):
    viz = ReportVisualizer()
    items = viz.build_all("sb_legacy", str(tmp_path), {"quantitative": LEGACY_ROWS})
    by_id = {e["id"]: e for e in items}
    assert "metric_trajectories" in by_id
    assert os.path.exists(str(tmp_path / by_id["metric_trajectories"]["path"]))
    html = (tmp_path / by_id["metric_trajectories"]["path"]).read_text(encoding="utf-8")
    assert "Key Metric Trajectories" in html
    assert "Tesla Optimus" in html


# ============================ (e) 真实 handoff 工件 ============================

@pytest.mark.skipif(not os.path.isfile(_REAL_HANDOFF_ENRICHED),
                    reason="real handoff artifacts not present")
def test_real_handoff_family_grouping_or_safe_degrade():
    """新字段落地 → 按家族分组产出 ≥1 面板；未落地 → 回退路径 degrade-safe（不抛、不臆造）。"""
    rows = json.load(open(_REAL_HANDOFF_ENRICHED, encoding="utf-8"))
    has_family = any(isinstance(r, dict) and r.get("metric_family") for r in rows)
    panels = _metric_trajectory_panels(rows)  # 绝不抛异常
    assert isinstance(panels, list)
    if has_family:
        assert len(panels) >= 1, "enrichment landed but no family trajectory grouped"
    else:
        # 未落地：本文件指标名高度唯一，折叠名回退合理地为空——但必须安全降级。
        # 用带 geography/period_end 的真实行模拟落地，证明家族路径能分组。
        enriched = []
        for r in rows:
            r2 = dict(r)
            r2.setdefault("metric_family", "humanoid unit volume"
                          if r.get("unit") == "units" else "humanoid economics")
            enriched.append(r2)
        grouped = _prepare_metric_family_trajectories(enriched)
        assert len(grouped) >= 1


@needs_plotly
@pytest.mark.skipif(not os.path.isfile(_REAL_HANDOFF_ENRICHED),
                    reason="real handoff artifacts not present")
def test_real_handoff_build_all_is_degrade_safe(tmp_path):
    """真实（未富化）工件跑 build_all：metric_trajectories 要么产出、要么诚实记跳过，绝不崩。"""
    rows = json.load(open(_REAL_HANDOFF_ENRICHED, encoding="utf-8"))
    viz = ReportVisualizer()
    items = viz.build_all("sb_real", str(tmp_path), {"quantitative": rows})
    ids = {e["id"] for e in items}
    m = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    builders = ids | {s["builder"] for s in m["skipped"]}
    assert "metric_trajectories" in builders  # 无声跳过=缺陷


@pytest.mark.skipif(not os.path.isfile(_REAL_HANDOFF_LEGACY),
                    reason="real legacy handoff artifacts not present")
def test_real_legacy_handoff_fallback_yields_series():
    """真实老工件（指标名内嵌年份）：折叠名回退路径确能产出 ≥1 序列。"""
    rows = json.load(open(_REAL_HANDOFF_LEGACY, encoding="utf-8"))
    assert not any(isinstance(r, dict) and r.get("metric_family") for r in rows)
    panels = _metric_trajectory_panels(rows)
    assert len(panels) >= 1
