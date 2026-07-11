"""End-to-end API regressions for the report publication barrier.

Completed metadata and an on-disk ``full_report.md`` are not publication
evidence.  Customer-facing endpoints must expose report state while withholding
the report body until the final audit hard-passes for the exact Markdown bytes.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.report_agent import Report, ReportManager, ReportStatus


@pytest.fixture
def reports_tmp(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports_dir))
    return reports_dir


@pytest.fixture
def client(reports_tmp):
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _save_completed_report(
    report_id: str,
    simulation_id: str,
    *,
    markdown: str = "# Forecast\n\nA customer-visible conclusion.\n",
) -> str:
    report = Report(
        report_id=report_id,
        simulation_id=simulation_id,
        graph_id=f"graph_{simulation_id}",
        simulation_requirement="Forecast the outcome.",
        status=ReportStatus.COMPLETED,
        markdown_content=markdown,
        created_at="2026-07-11T00:00:00+00:00",
        completed_at="2026-07-11T00:01:00+00:00",
    )
    ReportManager.save_report(report)
    return markdown


def _write_passing_audit(report_id: str, markdown: str, *, lang: str | None = None) -> None:
    audit = {
        "policy_version": 3,
        "hard_passed": True,
        "hard_issues": [],
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "publish_gate": {"enabled": True, "passed": True},
        "structured_forecast": {"required": False, "valid": True},
        "citation_artifacts": {"required": False, "passed": True},
    }
    path = ReportManager._get_report_final_audit_path(report_id, lang)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(audit, handle)


def _assert_withheld(payload: dict, reason_fragment: str) -> None:
    assert payload["publishable"] is False
    assert payload["markdown_content"] == ""
    assert any(
        reason_fragment in reason.lower()
        for reason in payload["publication_issues"]
    )


def test_detail_by_simulation_and_list_withhold_missing_audit(client):
    report_id = "report_barrier_missing_audit"
    simulation_id = "sim_barrier_missing_audit"
    _save_completed_report(report_id, simulation_id)

    detail = client.get(f"/api/report/{report_id}")
    assert detail.status_code == 200
    _assert_withheld(detail.get_json()["data"], "final audit")

    by_simulation = client.get(f"/api/report/by-simulation/{simulation_id}")
    assert by_simulation.status_code == 200
    _assert_withheld(by_simulation.get_json()["data"], "final audit")

    listing = client.get(f"/api/report/list?simulation_id={simulation_id}")
    assert listing.status_code == 200
    listed = listing.get_json()["data"]
    assert len(listed) == 1
    _assert_withheld(listed[0], "final audit")


def test_detail_by_simulation_and_list_withhold_stale_audit_fingerprint(client):
    report_id = "report_barrier_stale_audit"
    simulation_id = "sim_barrier_stale_audit"
    markdown = _save_completed_report(report_id, simulation_id)
    _write_passing_audit(report_id, markdown)

    # Mutating the published artifact after audit invalidates publication even
    # when metadata still says completed and the old audit says hard-passed.
    with open(ReportManager._get_report_markdown_path(report_id), "a", encoding="utf-8") as handle:
        handle.write("\nPost-audit mutation.\n")

    detail = client.get(f"/api/report/{report_id}")
    assert detail.status_code == 200
    _assert_withheld(detail.get_json()["data"], "fingerprint")

    by_simulation = client.get(f"/api/report/by-simulation/{simulation_id}")
    assert by_simulation.status_code == 200
    _assert_withheld(by_simulation.get_json()["data"], "fingerprint")

    listing = client.get(f"/api/report/list?simulation_id={simulation_id}")
    assert listing.status_code == 200
    listed = listing.get_json()["data"]
    assert len(listed) == 1
    _assert_withheld(listed[0], "fingerprint")


def test_unpublishable_report_rejects_markdown_translation_and_pdf(client):
    report_id = "report_barrier_reject_exports"
    simulation_id = "sim_barrier_reject_exports"
    _save_completed_report(report_id, simulation_id)

    translated = "# 预测\n\n不应发布的译文。\n"
    with open(
        ReportManager._get_report_translation_path(report_id, "zh"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write(translated)

    for endpoint in (
        f"/api/report/{report_id}/download",
        f"/api/report/{report_id}/full_report.zh.md",
        f"/api/report/{report_id}/pdf",
    ):
        response = client.get(endpoint)
        assert response.status_code == 409, endpoint
        payload = response.get_json()
        assert payload["success"] is False
        assert payload["publication_issues"]


def test_unpublishable_completed_report_does_not_complete_sections_or_unlock_interview(
    client,
):
    report_id = "report_barrier_locked"
    simulation_id = "sim_barrier_locked"
    _save_completed_report(report_id, simulation_id)
    ReportManager._ensure_report_folder(report_id)
    with open(ReportManager._get_section_path(report_id, 1), "w", encoding="utf-8") as handle:
        handle.write("## Executive summary\n\nDraft section.\n")

    sections = client.get(f"/api/report/{report_id}/sections")
    assert sections.status_code == 200
    assert sections.get_json()["data"]["is_complete"] is False

    partial = client.get(f"/api/report/{report_id}/sections-partial")
    assert partial.status_code == 200
    assert partial.get_json()["done"] is False

    check = client.get(f"/api/report/check/{simulation_id}")
    assert check.status_code == 200
    check_data = check.get_json()["data"]
    assert check_data["has_report"] is True
    assert check_data["report_status"] == "completed"
    assert check_data["interview_unlocked"] is False


def test_exactly_audited_report_is_exposed_and_downloadable(client):
    report_id = "report_barrier_publishable"
    simulation_id = "sim_barrier_publishable"
    markdown = _save_completed_report(report_id, simulation_id)
    _write_passing_audit(report_id, markdown)

    detail = client.get(f"/api/report/{report_id}")
    assert detail.status_code == 200
    detail_data = detail.get_json()["data"]
    assert detail_data["publishable"] is True
    assert detail_data["publication_issues"] == []
    assert detail_data["markdown_content"] == markdown

    download = client.get(f"/api/report/{report_id}/download")
    assert download.status_code == 200
    assert download.mimetype == "text/markdown"
    assert download.get_data(as_text=True) == markdown
