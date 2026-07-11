"""SDK forecasts and calibration must obey the exact publication barrier."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from app.config import Config
from app.services.report_agent import Report, ReportManager, ReportStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(Config, "API_V1_ENABLED", True, raising=False)
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _save_report(report_id: str, *, publish: bool) -> None:
    markdown = "# Forecast\n\nThe base case remains most likely.\n"
    forecast = {
        "scenarios": [
            {
                "name": "Base case",
                "probability": 0.7,
                "resolution_criteria": "The audited result records the base case.",
            },
            {
                "name": "Other",
                "probability": 0.3,
                "resolution_criteria": "Any other audited result.",
            },
        ],
        "binary_forecasts": [{
            "id": "F1",
            "statement": "The base case occurs.",
            "probability": 0.7,
            "resolution_criteria": "The audited result records the base case.",
        }],
    }
    ReportManager.save_report(Report(
        report_id=report_id,
        simulation_id=f"sim_{report_id}",
        graph_id="graph_sdk",
        simulation_requirement="Forecast the outcome.",
        status=ReportStatus.COMPLETED,
        markdown_content=markdown,
    ))
    forecast_path = os.path.join(
        ReportManager._get_report_folder(report_id), "forecast.json"
    )
    forecast_text = json.dumps(forecast, ensure_ascii=False, indent=2)
    with open(forecast_path, "w", encoding="utf-8") as handle:
        handle.write(forecast_text)
    if not publish:
        return
    audit = {
        "policy_version": Config.REPORT_FINAL_AUDIT_POLICY_VERSION,
        "hard_passed": True,
        "hard_issues": [],
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "forecast_sha256": hashlib.sha256(forecast_text.encode("utf-8")).hexdigest(),
        "publish_gate": {"enabled": True, "passed": True},
        "structured_forecast": {
            "required": True,
            "present": True,
            "valid": True,
        },
        "scenario_contract": {"valid": True, "issue_count": 0},
        "citation_artifacts": {"required": False, "passed": True},
    }
    with open(
        ReportManager._get_report_final_audit_path(report_id),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(audit, handle)


def test_sdk_withholds_and_refuses_to_score_unpublished_forecast(client):
    report_id = "report_sdk_blocked"
    _save_report(report_id, publish=False)

    read = client.get(f"/api/v1/forecast/{report_id}")
    resolve = client.post(
        f"/api/v1/resolve/{report_id}", json={"outcome": "Base case"}
    )

    assert read.status_code == 409
    assert resolve.status_code == 409
    assert "发布完整性门" in read.get_json()["error"]
    assert not os.path.exists(os.path.join(
        ReportManager._get_report_folder(report_id), "resolved.json"
    ))


def test_sdk_reads_current_policy_forecast(client):
    report_id = "report_sdk_publishable"
    _save_report(report_id, publish=True)

    response = client.get(f"/api/v1/forecast/{report_id}")

    assert response.status_code == 200
    assert response.get_json()["data"]["forecast"]["scenarios"][0]["name"] == "Base case"
