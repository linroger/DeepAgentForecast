"""LOOP-004: report visualization manifest producer/API contract.

Offline-only. Covers legacy list manifests, schema-v2 manifests emitted by
ReportVisualizer, malformed input degradation, and asset-path containment.
"""

import json
import os

import pytest

from app.services.report_agent import ReportManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _write_manifest(report_id, payload):
    folder = ReportManager._ensure_report_folder(report_id)
    with open(f"{folder}/viz_manifest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_v2_manifest_returns_items_and_skipped_reasons(client):
    rid = "report_viz_v2"
    items = [
        {
            "id": "scenario_probabilities",
            "type": "html",
            "path": "charts/scenario_probabilities.html",
            "png_path": "charts/scenario_probabilities.png",
            "caption": "Scenario probabilities",
            "placement_hint": "scenario",
        },
        {
            "id": "actor_network",
            "type": "html",
            "path": "charts/actor_network.html",
            "png_path": "charts/actor_network.png",
            "caption": "Actor network",
            "placement_hint": "actors",
        },
    ]
    skipped = [{"id": "calibration", "reason": "no historical outcomes"}]
    _write_manifest(rid, {"schema_version": 2, "items": items, "skipped": skipped})

    resp = client.get(f"/api/report/{rid}/viz-manifest")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["schema_version"] == 2
    assert body["data"] == items
    assert body["count"] == 2
    assert body["skipped"] == skipped


def test_legacy_list_manifest_remains_compatible(client):
    rid = "report_viz_legacy"
    items = [{"type": "png", "path": "charts/legacy.png", "caption": "Legacy"}]
    _write_manifest(rid, items)

    body = client.get(f"/api/report/{rid}/viz-manifest").get_json()

    assert body["success"] is True
    assert body["schema_version"] == 1
    assert body["data"] == items
    assert body["count"] == 1
    assert body["skipped"] == []


def test_manifest_rejects_unsafe_asset_paths(client):
    rid = "report_viz_paths"
    _write_manifest(rid, {
        "schema_version": 2,
        "items": [
            {"type": "html", "path": "../../secret.html", "png_path": "charts/good.png"},
            {"type": "html", "path": "charts/%2e%2e/viz-manifest"},
            {"type": "html", "path": "charts/%252e%252e/viz-manifest"},
            {"type": "html", "path": "charts/.\n./viz-manifest"},
            {"type": "html", "path": "charts/.\t./viz-manifest"},
            {"type": "html", "path": "charts/.\r./viz-manifest"},
            {"type": "html", "path": "charts/good.html", "png_path": "/tmp/leak.png"},
            {"type": "png", "path": "charts/ok.png"},
        ],
        "skipped": [],
    })

    body = client.get(f"/api/report/{rid}/viz-manifest").get_json()

    assert [item["path"] for item in body["data"]] == ["charts/good.html", "charts/ok.png"]
    assert "png_path" not in body["data"][0]


def test_malformed_v2_manifest_degrades_to_empty(client):
    rid = "report_viz_bad"
    _write_manifest(rid, {"schema_version": 2, "items": "not-a-list", "skipped": {}})

    resp = client.get(f"/api/report/{rid}/viz-manifest")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "success": True,
        "schema_version": 2,
        "data": [],
        "count": 0,
        "skipped": [],
    }


def test_interactive_chart_is_served_in_opaque_script_sandbox(client):
    rid = "report_viz_html"
    folder = ReportManager._ensure_report_folder(rid)
    charts = os.path.join(folder, "charts")
    os.makedirs(charts)
    with open(os.path.join(charts, "interactive.html"), "w", encoding="utf-8") as f:
        f.write("<html><script>document.body.dataset.ok='1'</script></html>")

    resp = client.get(f"/api/report/{rid}/charts/interactive.html")

    assert resp.status_code == 200
    assert "sandbox allow-scripts" in resp.headers["Content-Security-Policy"]
    assert "connect-src 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
