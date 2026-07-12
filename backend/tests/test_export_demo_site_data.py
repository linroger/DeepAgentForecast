from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.export_demo_site_data import (
    MARKDOWN_LINK_RE,
    RUNS,
    copy_markdown_assets,
    copy_report_assets,
    rebase_markdown_assets,
    rebase_report_assets,
    validate_retained_graph,
    validate_publishable_run,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _completed_state(report_id: str = "report_ok") -> dict:
    return {
        "pipeline_id": "pipe_ok",
        "status": "completed",
        "report_id": report_id,
        "simulation_id": "sim_ok",
        "graph_id": "graph_ok",
        "stages": {
            name: {"status": "completed", "error": None}
            for name in ("research", "ontology", "graph", "prepare", "run", "report")
        },
    }


def test_ev_demo_points_to_latest_verified_pipeline() -> None:
    assert RUNS["ev-2035"] == "pipe_91aaf91f6392"


def test_validate_publishable_run_checks_terminal_audit_and_hashes(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "report_ok"
    report_dir.mkdir(parents=True)
    report_bytes = b"# Audited report\n"
    forecast_bytes = b'{"scenarios": []}\n'
    (report_dir / "full_report.md").write_bytes(report_bytes)
    (report_dir / "forecast.json").write_bytes(forecast_bytes)
    _write_json(
        report_dir / "final_audit.json",
        {
            "report_id": "report_ok",
            "read_only": True,
            "disk_matches_memory": True,
            "hard_passed": True,
            "publish_gate": {"passed": True},
            "scenario_contract": {"valid": True},
            "markdown_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "forecast_sha256": hashlib.sha256(forecast_bytes).hexdigest(),
        },
    )

    audit = validate_publishable_run("pipe_ok", _completed_state(), uploads=str(tmp_path))

    assert audit["hard_passed"] is True


def test_validate_publishable_run_fails_closed_on_publish_gate(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports" / "report_ok"
    report_dir.mkdir(parents=True)
    report_bytes = b"# Report\n"
    forecast_bytes = b"{}\n"
    (report_dir / "full_report.md").write_bytes(report_bytes)
    (report_dir / "forecast.json").write_bytes(forecast_bytes)
    _write_json(
        report_dir / "final_audit.json",
        {
            "report_id": "report_ok",
            "read_only": True,
            "disk_matches_memory": True,
            "hard_passed": True,
            "publish_gate": {"passed": False},
            "scenario_contract": {"valid": True},
            "markdown_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "forecast_sha256": hashlib.sha256(forecast_bytes).hexdigest(),
        },
    )

    with pytest.raises(RuntimeError, match="publish gate did not pass"):
        validate_publishable_run("pipe_ok", _completed_state(), uploads=str(tmp_path))


def test_report_assets_are_copied_and_rebased_without_touching_external_images(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "report"
    output_dir = tmp_path / "site"
    chart = report_dir / "charts" / "scenario.png"
    chart.parent.mkdir(parents=True)
    chart.write_bytes(b"png")
    markdown = (
        "![Scenario](charts/scenario.png)\n"
        "![External](https://example.com/chart.png)\n"
    )

    copied = copy_report_assets(markdown, str(report_dir), str(output_dir))
    rebased = rebase_report_assets(markdown, "ev-2035")

    assert copied == ["charts/scenario.png"]
    assert (output_dir / "charts" / "scenario.png").read_bytes() == b"png"
    assert "![Scenario](demos/ev-2035/charts/scenario.png)" in rebased
    assert "![External](https://example.com/chart.png)" in rebased


def test_report_asset_copy_rejects_missing_referenced_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="referenced Markdown asset is missing"):
        copy_report_assets(
            "![Missing](charts/missing.png)\n",
            str(tmp_path / "report"),
            str(tmp_path / "site"),
        )


def test_dossier_images_and_links_use_an_isolated_asset_namespace(tmp_path: Path) -> None:
    source_dir = tmp_path / "handoff"
    output_dir = tmp_path / "site"
    (source_dir / "charts").mkdir(parents=True)
    (source_dir / "charts" / "timeline.png").write_bytes(b"png")
    (source_dir / "charts" / "timeline.html").write_text(
        "<html>  \n<body>ok</body>\t\n</html>\n",
        encoding="utf-8",
    )
    markdown = (
        "![Timeline](charts/timeline.png)\n"
        "[Interactive version](charts/timeline.html)\n"
    )

    copied = copy_markdown_assets(
        markdown,
        str(source_dir),
        str(output_dir),
        destination_namespace="research-charts",
    )
    rebased = rebase_markdown_assets(
        markdown,
        "ev-2035",
        destination_namespace="research-charts",
    )

    assert copied == [
        "research-charts/timeline.png",
        "research-charts/timeline.html",
    ]
    assert (output_dir / "research-charts" / "timeline.png").read_bytes() == b"png"
    assert (output_dir / "research-charts" / "timeline.html").read_text(encoding="utf-8") == (
        "<html>\n<body>ok</body>\n</html>\n"
    )
    assert "![Timeline](demos/ev-2035/research-charts/timeline.png)" in rebased
    assert "[Interactive version](demos/ev-2035/research-charts/timeline.html)" in rebased


@pytest.mark.parametrize(
    "target",
    [
        "charts/../../secret.png",
        "charts/%2e%2e/secret.png",
        "charts/chart name.png",
        "assets/unsupported.png",
        "/charts/absolute.png",
        r"charts\backslash.png",
    ],
)
def test_report_asset_parser_fails_closed_on_unsafe_local_targets(target: str) -> None:
    with pytest.raises(ValueError, match="unsafe local Markdown asset"):
        rebase_report_assets(f"![Unsafe]({target})\n", "ev-2035")


def test_report_asset_query_and_fragment_are_preserved_safely(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    output_dir = tmp_path / "site"
    chart = report_dir / "charts" / "scenario.png"
    chart.parent.mkdir(parents=True)
    chart.write_bytes(b"png")
    markdown = "![Scenario](charts/scenario.png?v=20260713#probability)\n"

    copied = copy_report_assets(markdown, str(report_dir), str(output_dir))
    rebased = rebase_report_assets(markdown, "ev-2035")

    assert copied == ["charts/scenario.png"]
    assert (output_dir / "charts" / "scenario.png").is_file()
    assert (
        "![Scenario](demos/ev-2035/charts/scenario.png?v=20260713#probability)"
        in rebased
    )


def test_report_asset_quoted_markdown_title_is_preserved(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    output_dir = tmp_path / "site"
    chart = report_dir / "charts" / "scenario.png"
    chart.parent.mkdir(parents=True)
    chart.write_bytes(b"png")
    markdown = '[Scenario](charts/scenario.png "Probability chart")\n'

    copied = copy_report_assets(markdown, str(report_dir), str(output_dir))
    rebased = rebase_report_assets(markdown, "ev-2035")

    assert copied == ["charts/scenario.png"]
    assert (
        '[Scenario](demos/ev-2035/charts/scenario.png "Probability chart")'
        in rebased
    )


def test_strict_skip_graph_accepts_only_the_current_graph(tmp_path: Path) -> None:
    _write_json(tmp_path / "graph.json", {"graph_id": "graph_current"})

    validate_retained_graph(str(tmp_path), "graph_current")

    with pytest.raises(RuntimeError, match="stale graph.json"):
        validate_retained_graph(str(tmp_path), "graph_stale")


def test_published_ev_markdown_assets_resolve_and_match_manifest_hashes() -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs"
    demo_dir = docs_root / "demos" / "ev-2035"
    meta = json.loads((demo_dir / "meta.json").read_text(encoding="utf-8"))

    for relative_path, expected_sha256 in meta["artifact_sha256"].items():
        artifact = demo_dir / relative_path
        assert artifact.is_file(), relative_path
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected_sha256

    expected_targets = {
        f"demos/ev-2035/{relative_path}"
        for relative_path in meta["dossier_assets"] + meta["report_assets"]
    }
    actual_targets = set()
    for markdown_name in ("dossier.md", "report.md"):
        markdown = (demo_dir / markdown_name).read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(markdown):
            target = match.group("target")
            if target.startswith("demos/ev-2035/"):
                actual_targets.add(target.split("?", 1)[0].split("#", 1)[0])

    assert actual_targets == expected_targets
