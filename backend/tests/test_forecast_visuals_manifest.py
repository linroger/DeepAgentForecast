"""Focused lifecycle checks for the bundled research-chart manifest renderer."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RENDERER_PATH = (
    REPO_ROOT
    / "deerflow_bridge"
    / "skills"
    / "forecast-visuals"
    / "scripts"
    / "render.py"
)


def _load_renderer():
    spec = importlib.util.spec_from_file_location("forecast_visuals_manifest_renderer", RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, rows) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_merge_replaces_html_only_actor_row_with_png_capable_rerun(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    _write_manifest(
        manifest,
        [
            {
                "title": "Actor Relationship Network",
                "source_data": "actors.json",
                "path": "charts/actor_network.html",
            }
        ],
    )

    refreshed = {
        "id": "actor_network",
        "title": "Actor Relationship Network",
        "source_data": "actors.json",
        "path": "charts/actor_network.png",
        "html_path": "charts/actor_network.html",
    }
    merged = renderer._merge_manifest(manifest, [refreshed])

    assert merged == [refreshed]
    assert sum(renderer._owned_chart_id(row) == "actor_network" for row in merged) == 1


def test_main_removes_all_stale_owned_rows_when_inputs_are_absent(tmp_path, monkeypatch):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    custom = {
        "id": "custom_market_map",
        "title": "Custom market map",
        "path": "charts/custom_market_map.svg",
    }
    _write_manifest(
        manifest,
        [
            {"source_data": "actors.json", "path": "charts/actor_network.png"},
            {"source_data": "timeline.json", "path": "charts/timeline.html"},
            {"source_data": "quantitative.json", "path": "charts/quant_metrics.png"},
            {
                "source_data": "prediction_markets.json",
                "path": "charts/market_probabilities.png",
            },
            {"source_data": "sources.json", "path": "charts/source_quality.png"},
            custom,
        ],
    )
    # No renderer runs when every input is absent; one available backend only bypasses
    # the command's library-presence guard so the cleanup lifecycle can execute.
    monkeypatch.setattr(renderer, "_HAS_MPL", True)
    monkeypatch.setattr(sys, "argv", [str(RENDERER_PATH), "--dir", str(tmp_path)])

    assert renderer.main() == 2
    assert json.loads(manifest.read_text(encoding="utf-8")) == [custom]


def test_merge_preserves_custom_rows_even_when_their_path_looks_owned(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    custom = {
        "id": "custom_actor_annotation",
        "title": "Editorial actor annotation",
        "path": "charts/actor_network.html",
    }
    stale_timeline = {
        "source_data": "timeline.json",
        "path": "charts/timeline.html",
    }
    current_quant = {
        "id": "quant_metrics",
        "source_data": "quantitative.json",
        "path": "charts/quant_metrics.png",
    }
    _write_manifest(manifest, [custom, stale_timeline])

    assert renderer._merge_manifest(manifest, [current_quant]) == [custom, current_quant]


def test_merge_preserves_idless_custom_chart_that_shares_renderer_source(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    custom = {
        "title": "Custom actor breakdown",
        "source_data": "actors.json",
        "path": "charts/custom_actor_breakdown.svg",
    }
    _write_manifest(manifest, [custom])

    assert renderer._merge_manifest(manifest, []) == [custom]
    assert renderer._owned_chart_id(custom) is None


def test_merge_handles_malformed_legacy_rows_and_is_deterministic(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    anonymous = {}
    malformed = {"id": 17, "path": None, "source_data": ["actors.json"]}
    custom = {"path": "charts/freeform.svg", "caption": "Freeform"}
    _write_manifest(
        manifest,
        [
            None,
            "not-a-row",
            anonymous,
            malformed,
            {"html_path": "charts/quant_metrics.html"},
            custom,
        ],
    )
    current_timeline = {
        "id": "timeline",
        "source_data": "timeline.json",
        "path": "charts/timeline.png",
        "html_path": "charts/timeline.html",
    }

    first = renderer._merge_manifest(manifest, [current_timeline, current_timeline.copy()])
    second = renderer._merge_manifest(manifest, [current_timeline, current_timeline.copy()])

    assert first == second == [anonymous, malformed, custom, current_timeline]


def test_prep_markets_filters_invalid_rows_deduplicates_and_ranks_by_volume():
    renderer = _load_renderer()
    prepared = renderer.prep_markets(
        {
            "as_of": "2026-07-11T08:00:00+00:00",
            "markets": [
                {
                    "market_id": "small",
                    "question": "Will the smaller market resolve yes?",
                    "implied_yes_prob": 0.42,
                    "volume": 1_000,
                },
                {
                    "market_id": "large",
                    "question": "Will the larger market resolve yes?",
                    "implied_yes_prob": 73,
                    "volume": "5000",
                    "liquidity": 900,
                },
                {
                    "market_id": "large",
                    "question": "Duplicate lower-volume copy",
                    "implied_yes_prob": 0.74,
                    "volume": 100,
                },
                {"market_id": "settled", "question": "Settled?", "implied_yes_prob": 1.0},
                {"market_id": "missing-question", "implied_yes_prob": 0.2},
            ],
        }
    )

    assert prepared is not None
    assert prepared["as_of"] == "2026-07-11T08:00:00+00:00"
    # Horizontal bars are stored least-liquid first so the most-liquid row renders on top.
    assert [row["market_id"] for row in prepared["markets"]] == ["small", "large"]
    assert prepared["markets"][-1]["probability"] == 0.73
    assert prepared["markets"][-1]["liquidity"] == 900.0


def test_prep_sources_reports_deduplicated_tier_and_freshness_mix():
    renderer = _load_renderer()
    prepared = renderer.prep_sources(
        [
            {"url": "https://example.com/a/", "title": "Primary", "tier": "S1", "date": "2026-07-10"},
            {"url": "https://EXAMPLE.com/a", "title": "Duplicate", "tier": "S4", "date": "2020-01-01"},
            {"url": "https://example.com/b", "title": "Analysis", "tier": "s3", "date": "2026-05-01"},
            {"url": "https://example.com/c", "title": "Old filing", "tier": "S2", "staleness_days": 400},
            {"title": "Undated source"},
        ]
    )

    assert prepared is not None
    assert prepared["total"] == 4
    assert prepared["reference_date"] == "2026-07-10"
    assert prepared["explicit_staleness_count"] == 1
    assert {row["label"]: row["count"] for row in prepared["tiers"]} == {
        "S1": 1,
        "S2": 1,
        "S3": 1,
        "S4": 0,
        "Untiered": 1,
    }
    assert {row["label"]: row["count"] for row in prepared["freshness"]} == {
        "≤30 days": 1,
        "31–90 days": 1,
        "91–365 days": 0,
        ">365 days": 1,
        "Undated": 1,
    }


def test_source_identity_normalizes_host_but_preserves_case_sensitive_path():
    renderer = _load_renderer()

    assert renderer._source_identity({"url": "HTTPS://EXAMPLE.COM/Report/"}) == (
        "https://example.com/Report"
    )
    assert renderer._source_identity({"url": "https://example.com/report"}) == (
        "https://example.com/report"
    )


def test_market_and_source_ownership_replaces_legacy_rows_but_preserves_custom(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    custom = {
        "id": "custom_market_commentary",
        "source_data": "prediction_markets.json",
        "path": "charts/market_probabilities.png",
    }
    _write_manifest(
        manifest,
        [
            custom,
            {
                "source_data": "prediction_markets.json",
                "path": "charts/market_probabilities.html",
            },
            {"source_data": "sources.json", "path": "charts/source_quality.html"},
        ],
    )
    current_market = {
        "id": "market_probabilities",
        "source_data": "prediction_markets.json",
        "path": "charts/market_probabilities.png",
    }
    current_sources = {
        "id": "source_quality",
        "source_data": "sources.json",
        "path": "charts/source_quality.png",
    }

    assert renderer._merge_manifest(manifest, [current_sources, current_market]) == [
        custom,
        current_market,
        current_sources,
    ]


def test_timeline_matplotlib_fallback_annotates_each_event_once(tmp_path, monkeypatch):
    renderer = _load_renderer()

    class FakeAxes:
        def __init__(self):
            self.annotations = []

        def scatter(self, *args, **kwargs):
            return None

        def annotate(self, *args, **kwargs):
            self.annotations.append((args, kwargs))

        def set_title(self, *args, **kwargs):
            return None

        def set_ylim(self, *args, **kwargs):
            return None

        def set_yticks(self, *args, **kwargs):
            return None

        def set_xticks(self, *args, **kwargs):
            return None

    class FakeFigure:
        def tight_layout(self):
            return None

        def savefig(self, path):
            Path(path).touch()

    class FakePyplot:
        def __init__(self):
            self.axes = FakeAxes()

        def subplots(self, *args, **kwargs):
            return FakeFigure(), self.axes

        def close(self, *args, **kwargs):
            return None

    fake_plt = FakePyplot()
    monkeypatch.setattr(renderer, "_HAS_PLOTLY", False)
    monkeypatch.setattr(renderer, "_HAS_MPL", True)
    monkeypatch.setattr(renderer, "plt", fake_plt)
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    prepared = renderer.prep_timeline(
        [
            {"date": "2026-07-01", "event": "First"},
            {"date": "2026-07-02", "event": "Second"},
            {"date": "2026-07-03", "event": "Third"},
        ]
    )

    png_path, html_path = renderer.render_timeline(prepared, charts_dir)

    assert png_path == "charts/timeline.png"
    assert html_path is None
    assert len(fake_plt.axes.annotations) == 3


def test_main_registers_market_and_source_figures(tmp_path, monkeypatch):
    renderer = _load_renderer()
    (tmp_path / "prediction_markets.json").write_text(
        json.dumps(
            {
                "as_of": "2026-07-11T08:00:00+00:00",
                "markets": [
                    {
                        "market_id": "m1",
                        "question": "Will X happen?",
                        "implied_yes_prob": 0.61,
                        "volume": 5_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text(
        json.dumps([{"url": "https://example.com", "tier": "S1", "date": "2026-07-10"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(renderer, "_HAS_MPL", True)
    monkeypatch.setattr(
        renderer,
        "render_markets",
        lambda prep, charts_dir: (
            "charts/market_probabilities.png",
            "charts/market_probabilities.html",
        ),
    )
    monkeypatch.setattr(
        renderer,
        "render_sources",
        lambda prep, charts_dir: ("charts/source_quality.png", "charts/source_quality.html"),
    )
    monkeypatch.setattr(sys, "argv", [str(RENDERER_PATH), "--dir", str(tmp_path)])

    assert renderer.main() == 0
    manifest = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in manifest] == ["market_probabilities", "source_quality"]
    assert all(row["path"].endswith(".png") for row in manifest)
    assert all(row["html_path"].endswith(".html") for row in manifest)


def test_plotly_html_has_stable_div_id_and_is_byte_deterministic(tmp_path, monkeypatch):
    renderer = _load_renderer()
    if not renderer._HAS_PLOTLY:
        pytest.skip("Plotly is optional; deterministic HTML is exercised when it is installed")
    monkeypatch.setattr(renderer, "_HAS_MPL", False)
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    prepared = renderer.prep_markets(
        {
            "as_of": "2026-07-11T08:00:00+00:00",
            "markets": [
                {
                    "market_id": "m1",
                    "question": "Will X happen?",
                    "implied_yes_prob": 0.61,
                    "volume": 5_000,
                }
            ],
        }
    )

    renderer.render_markets(prepared, charts_dir)
    first = (charts_dir / "market_probabilities.html").read_bytes()
    renderer.render_markets(prepared, charts_dir)
    second = (charts_dir / "market_probabilities.html").read_bytes()

    assert first == second
    assert b'id="forecast-visual-market_probabilities"' in first
