"""Serve-time plotly inlining for sandboxed chart HTML (utils/chart_html).

Directory-mode charts (include_plotlyjs='directory') reference a sibling
plotly.min.js that the charts API's opaque CSP sandbox would block. The API
splices the bundle into the HTML at serve time; these tests pin that contract:
inlining happens, the sandbox header stays, and unsafe/missing bundles fail
closed to the unmodified file.
"""

import pytest

from app.services.report_agent import ReportManager
from app.utils.chart_html import inline_plotly_bundle

CHART_HTML = (
    "<html><head></head><body><div id=\"c\"></div>"
    "<script src=\"plotly.min.js\"></script>"
    "<script>/*init*/</script></body></html>"
)
BUNDLE_JS = "window.Plotly = {newPlot: function () {}};"


def _write_chart(charts_dir, with_bundle=True, html=CHART_HTML, bundle=BUNDLE_JS):
    charts_dir.mkdir(parents=True, exist_ok=True)
    (charts_dir / "chart.html").write_text(html, encoding="utf-8")
    if with_bundle:
        (charts_dir / "plotly.min.js").write_text(bundle, encoding="utf-8")
    return charts_dir / "chart.html"


# ---------------------------------------------------------------- helper unit

def test_inlines_sibling_bundle_and_removes_src_reference(tmp_path):
    html_path = _write_chart(tmp_path / "charts")
    result = inline_plotly_bundle(str(html_path))
    assert result is not None
    assert "window.Plotly" in result
    assert "plotly.min.js" not in result
    assert "/*init*/" in result  # 其余脚本原样保留


def test_returns_none_without_bundle_reference(tmp_path):
    html_path = _write_chart(
        tmp_path / "charts", html="<html><body><script>inline()</script></body></html>")
    assert inline_plotly_bundle(str(html_path)) is None


def test_returns_none_when_bundle_missing(tmp_path):
    html_path = _write_chart(tmp_path / "charts", with_bundle=False)
    assert inline_plotly_bundle(str(html_path)) is None


def test_fails_closed_on_script_terminator_inside_bundle(tmp_path):
    html_path = _write_chart(
        tmp_path / "charts", bundle="var x = '</script><img src=x>';")
    assert inline_plotly_bundle(str(html_path)) is None


def test_bundle_cache_invalidates_on_content_change(tmp_path):
    html_path = _write_chart(tmp_path / "charts")
    first = inline_plotly_bundle(str(html_path))
    assert first is not None and "newPlot" in first
    import os
    bundle = tmp_path / "charts" / "plotly.min.js"
    bundle.write_text("window.Plotly = {v2: true};", encoding="utf-8")
    os.utime(bundle, ns=(1, 1))  # 强制 mtime 变化（同秒写入也要失效）
    second = inline_plotly_bundle(str(html_path))
    assert second is not None and "v2" in second


# ---------------------------------------------------------------- report API

@pytest.fixture
def client(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(
        ReportManager, "get_report", classmethod(lambda cls, report_id: object()))
    monkeypatch.setattr(
        ReportManager, "is_publishable",
        classmethod(lambda cls, report_id, lang=None: True))
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _report_charts_dir(report_id):
    import pathlib
    return pathlib.Path(ReportManager._ensure_report_folder(report_id)) / "charts"


def test_served_chart_html_has_bundle_inlined_and_sandbox_intact(client):
    _write_chart(_report_charts_dir("report_inline"))
    resp = client.get("/api/report/report_inline/charts/chart.html")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "window.Plotly" in body
    assert "plotly.min.js" not in body
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "sandbox allow-scripts" in csp
    assert "script-src 'unsafe-inline'" in csp


def test_served_chart_html_without_bundle_falls_back_unmodified(client):
    _write_chart(_report_charts_dir("report_nobundle"), with_bundle=False)
    resp = client.get("/api/report/report_nobundle/charts/chart.html")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "plotly.min.js" in body  # 原样：内置的「bundle 不可用」提示接管
    assert "sandbox allow-scripts" in resp.headers.get("Content-Security-Policy", "")


def test_direct_bundle_fetch_stays_blocked(client):
    _write_chart(_report_charts_dir("report_blockjs"))
    resp = client.get("/api/report/report_blockjs/charts/plotly.min.js")
    assert resp.status_code == 404  # .js 不在扩展白名单，且无需放行
