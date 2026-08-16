"""TASK 4 (audit): metric-trajectory x-axis ordering + shared plotly bundle.

Shipped-chart bugs under test:
  (a) _save_html inlined the full 4.86MB plotly.js into every chart HTML
      (~40MB/report, 1.9GB accumulated) → switch to a single shared
      plotly.min.js written next to the charts ('directory' mode);
  (b) build_metric_trajectories_html plotted string x categories → plotly
      category axes ordered by FIRST APPEARANCE, so a single-point 2027 line
      drawn first pushed 2027 left of 2025 (reversed year axis); single-point
      traces also rendered a meaningless 'lines+markers' mode, and the
      horizontal legend overlapped the first subplot title.

Figures are inspected directly (captured via _save_pair) — no HTML parsing.
"""

import os

import pytest

import app.services.report_visualizer as rv
from app.services.report_visualizer import ReportVisualizer

pytestmark = pytest.mark.skipif(not rv.PLOTLY_AVAILABLE,
                                reason="plotly not installed")


def _row(family, region, year, value):
    """One publishable quantitative row (actual observation, full provenance)."""
    return {
        "metric_family": family,
        "metric": f"{family} — {region}",
        "region": region,
        "year": year,
        "value_num": value,
        "value": str(value),
        "unit": "USD per kWh",
        "value_kind": "actual",
        "source": "IEA Global EV Outlook 2025",
        "definition": "Pack-level battery price, volume-weighted average",
        "as_of_date": "2025-03-01",
    }


@pytest.fixture()
def viz(monkeypatch):
    monkeypatch.setattr(ReportVisualizer, "_png_export_ok", lambda self: False)
    return ReportVisualizer()


@pytest.fixture()
def trajectory_fig(viz, monkeypatch, tmp_path):
    """Family with line A = one point (2027) and line B = points (2025, 2026).

    Line A sorts first ('Alpha' < 'Beta'), reproducing the first-appearance
    category ordering that shipped reversed year axes (2027 left of 2025).
    """
    captured = {}

    def _capture(self, fig, charts_dir, stem, item_id=None):
        captured["fig"] = fig
        return os.path.join("charts", f"{stem}.html")

    monkeypatch.setattr(ReportVisualizer, "_save_pair", _capture)
    quantitative = [
        _row("Battery pack price", "Alpha", 2027, 80.0),
        _row("Battery pack price", "Beta", 2026, 95.0),
        _row("Battery pack price", "Beta", 2025, 110.0),
    ]
    rel = viz.build_metric_trajectories_html(quantitative, str(tmp_path / "charts"))
    assert rel is not None, "trajectory builder must produce a chart for this family"
    return captured["fig"]


def test_year_axis_is_numeric_and_ascending(trajectory_fig):
    traces = list(trajectory_fig.data)
    assert len(traces) == 2
    for tr in traces:
        xs = list(tr.x)
        assert all(isinstance(x, int) and not isinstance(x, bool) for x in xs), (
            f"expected numeric year x values (ascending axis), got {xs!r}")
        assert xs == sorted(xs)
    by_name = {tr.name: tr for tr in traces}
    assert list(by_name["Alpha"].x) == [2027]
    assert list(by_name["Beta"].x) == [2025, 2026]


def test_single_point_trace_uses_markers_only(trajectory_fig):
    by_name = {tr.name: tr for tr in trajectory_fig.data}
    assert by_name["Alpha"].mode == "markers", (
        "single-point traces must not request an invisible line")
    assert by_name["Beta"].mode == "lines+markers"


def test_multiline_legend_sits_below_plot_area(trajectory_fig):
    legend = trajectory_fig.layout.legend
    assert legend.orientation == "h"
    assert legend.y is not None and legend.y <= 0, (
        "horizontal legend must not overlap the first subplot title band")


def test_save_html_references_shared_plotly_bundle(viz, tmp_path):
    import plotly.graph_objects as go

    charts = tmp_path / "charts"
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))
    rel = viz._save_html(fig, str(charts), "axis_probe.html")
    assert rel == os.path.join("charts", "axis_probe.html")

    out = charts / "axis_probe.html"
    html = out.read_text(encoding="utf-8")
    assert 'src="plotly.min.js"' in html, "chart must reference the shared bundle"
    assert out.stat().st_size < 200_000, (
        f"chart HTML is {out.stat().st_size} bytes — inline plotly bundle came back")

    bundle = charts / "plotly.min.js"
    assert bundle.is_file(), "shared plotly.min.js must live next to the charts"
    assert bundle.stat().st_size > 1_000_000

    # A second chart reuses the same bundle (one copy per charts dir).
    before = bundle.stat().st_mtime_ns
    fig2 = go.Figure(go.Scatter(x=[1, 2], y=[5, 6]))
    assert viz._save_html(fig2, str(charts), "axis_probe_2.html") is not None
    assert bundle.stat().st_mtime_ns == before
