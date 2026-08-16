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


def test_main_demotes_actor_network_by_default_and_requires_context_opt_in(
    tmp_path, monkeypatch,
):
    renderer = _load_renderer()
    (tmp_path / "actors.json").write_text(
        json.dumps({
            "actors": [
                {"name": "A", "role_class": "principal"},
                {"name": "B", "role_class": "stakeholder"},
                {"name": "C", "role_class": "arbiter"},
            ],
            "relationships": [{"source": "A", "target": "B", "type": "SUPPLIES"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(renderer, "_HAS_MPL", True)
    monkeypatch.setattr(
        renderer,
        "render_network",
        lambda prep, charts_dir: ("charts/actor_network.png", "charts/actor_network.html"),
    )
    monkeypatch.setattr(sys, "argv", [str(RENDERER_PATH), "--dir", str(tmp_path)])

    assert renderer.main() == 2
    assert not (tmp_path / "charts.json").exists()

    monkeypatch.setattr(
        sys, "argv", [str(RENDERER_PATH), "--dir", str(tmp_path), "--context"]
    )
    assert renderer.main() == 0
    assert [r["id"] for r in json.loads(
        (tmp_path / "charts.json").read_text(encoding="utf-8")
    )] == ["actor_network"]


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


def test_update_manifest_locks_the_read_merge_write_transaction(tmp_path):
    renderer = _load_renderer()
    manifest = tmp_path / "charts.json"
    custom = {"id": "custom_chart", "path": "charts/custom.png"}
    current = {"id": "timeline", "path": "charts/timeline.png"}
    _write_manifest(manifest, [custom])

    merged = renderer._update_manifest(manifest, [current])

    assert merged == [custom, current]
    assert json.loads(manifest.read_text(encoding="utf-8")) == merged
    assert (tmp_path / "charts.json.lock").exists()


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


def test_prep_sources_uses_one_freshness_anchor_for_every_dated_row():
    renderer = _load_renderer()
    prepared = renderer.prep_sources([
        {"url": "https://example.com/old", "tier": "S1", "date": "2025-12-31",
         "staleness_days": 203},
        {"url": "https://example.com/new", "tier": "S2", "date": "2026-07-12"},
    ])

    assert prepared["reference_date"] == "2026-07-22"
    assert prepared["reference_source"] == "explicit_staleness_anchor"
    assert {row["label"]: row["count"] for row in prepared["freshness"]}["≤30 days"] == 1


def test_prep_sources_prefers_authoritative_run_anchor_over_staleness_inference():
    renderer = _load_renderer()
    prepared = renderer.prep_sources(
        [
            {"url": "https://example.com/a", "tier": "S1", "date": "2026-07-01",
             "staleness_days": 21},
            {"url": "https://example.com/b", "tier": "S2", "date": "2026-06-01",
             "staleness_days": 51},
        ],
        "2026-07-12T15:51:13+00:00",
    )

    assert prepared["reference_date"] == "2026-07-12"
    assert prepared["reference_source"] == "run_metadata"
    assert {row["label"]: row["count"] for row in prepared["freshness"]} == {
        "≤30 days": 1,
        "31–90 days": 1,
        "91–365 days": 0,
        ">365 days": 0,
        "Undated": 0,
    }


def test_prep_quant_builds_comparable_panels_not_largest_ambiguous_unit_group():
    renderer = _load_renderer()
    prepared = renderer.prep_quant(
        [
            {"metric": "Revenue", "value": 100, "unit": "USD billion"},
            {"metric": "Market size", "value": 155, "unit": "USD billion"},
            {"metric": "Margin", "value": 20, "unit": "%"},
            {"metric": "Share", "value": 52, "unit": "%"},
            {"metric": "China adoption", "value": 53, "unit": "% new car sales",
             "as_of_date": "2025-12-31", "source": "BNEF",
             "definition": "BEV+PHEV share of China passenger vehicle sales"},
            {"metric": "Europe adoption", "value": 28, "unit": "% new vehicle sales",
             "as_of_date": "2025-12-31", "source": "IEA",
             "definition": "BEV+PHEV share of European passenger vehicle sales"},
            {"metric": "BEV pack", "value": 99, "unit": "USD per kWh",
             "as_of_date": "2025-12-31", "source": "BNEF",
             "definition": "BEV-only battery pack price"},
            {"metric": "Storage pack", "value": 70, "unit": "USD/kWh",
             "as_of_date": "2025-12-31", "source": "BNEF",
             "definition": "BESS battery pack price"},
        ]
    )

    assert prepared is not None
    assert {panel["unit"] for panel in prepared["panels"]} == {
        "% new car sales", "USD per kWh",
    }
    assert all(panel["unit"] not in {"%", "USD billion"} for panel in prepared["panels"])


def test_prep_quant_uses_canonical_midpoint_and_preserves_explicit_range():
    renderer = _load_renderer()
    rows = [
        {"metric": "Current best intra-day LDES power capex", "value": "1100-1400",
         "value_num": 999, "unit": "USD/kW", "as_of_date": "2026-01-21",
         "source": "EPRI benchmark", "definition": "Intra-day LDES power capex"},
        {"metric": "2030 intra-day LDES power capex target", "value": "650",
         "value_num": 650, "unit": "USD/kW", "as_of_date": "2026-01-21",
         "source": "EPRI benchmark", "definition": "Intra-day LDES power capex target"},
    ]
    prepared = renderer.prep_quant(rows)
    assert prepared is not None
    bars = prepared["panels"][0]["bars"]
    ranged = next(bar for bar in bars if bar["metric"].startswith("Current best"))
    assert (ranged["value"], ranged["low"], ranged["high"]) == (1250.0, 1100.0, 1400.0)
    assert "LDES power capex" in prepared["panels"][0]["title"]


def test_prep_quant_requires_semantic_compatibility_and_provenance():
    renderer = _load_renderer()
    rows = [
        {"metric": "Region A EV share", "value": 41, "unit": "% new car sales",
         "as_of_date": "2025-12-31", "source": "Agency A",
         "definition": "Annual BEV+PHEV share of new vehicle sales"},
        {"metric": "Region B EV share", "value": 29, "unit": "% new vehicle sales",
         "as_of_date": "2025-12-31", "source": "Agency B",
         "definition": "Annual BEV+PHEV share of new vehicle sales"},
    ]

    assert renderer.prep_quant(rows) is not None

    mixed_denominator = [dict(row) for row in rows]
    mixed_denominator[1]["definition"] = "Annual BEV+PHEV share of total vehicle fleet"
    assert renderer.prep_quant(mixed_denominator) is None

    mixed_period = [dict(row) for row in rows]
    mixed_period[1]["definition"] = "Monthly BEV+PHEV share of new vehicle sales"
    assert renderer.prep_quant(mixed_period) is None

    mixed_as_of = [dict(row) for row in rows]
    mixed_as_of[1]["as_of_date"] = "2024-12-31"
    assert renderer.prep_quant(mixed_as_of) is None

    for missing_field in ("source", "as_of_date"):
        missing_provenance = [dict(row) for row in rows]
        missing_provenance[1].pop(missing_field)
        assert renderer.prep_quant(missing_provenance) is None


def test_negative_staleness_does_not_turn_future_dated_actual_into_projection():
    renderer = _load_renderer()
    row = {
        "metric": "Reported Q4 vehicle registrations",
        "definition": "Observed registrations during the quarter",
        "as_of_date": "2027-12-31",
        "staleness_days": -430,
    }

    assert renderer._is_projection(row) is False
    assert renderer._is_projection({**row, "definition": "Forecast registrations"}) is True


def test_prep_quant_drops_future_dated_actuals_but_keeps_forecasts():
    renderer = _load_renderer()
    future_actuals = [
        {"metric": "Region A reported EV share", "value": 41, "unit": "% new car sales",
         "as_of_date": "2027-12-31", "source": "Agency A",
         "definition": "Observed BEV+PHEV share of new vehicle sales",
         "staleness_days": -430},
        {"metric": "Region B reported EV share", "value": 29, "unit": "% new vehicle sales",
         "as_of_date": "2027-12-31", "source": "Agency B",
         "definition": "Observed BEV+PHEV share of new vehicle sales",
         "staleness_days": -430},
    ]
    assert renderer.prep_quant(future_actuals) is None

    forecasts = [
        {**row,
         "metric": row["metric"].replace("reported", "2030 forecast"),
         "definition": row["definition"].replace("Observed", "Forecast"),
         "as_of_date": "2030-12-31"}
        for row in future_actuals
    ]
    prepared = renderer.prep_quant(forecasts)
    assert prepared is not None
    assert all(point["projection"] is True for point in prepared["panels"][0]["bars"])


def test_prep_trajectories_uses_period_end_not_publication_date():
    renderer = _load_renderer()
    rows = [
        {
            "metric": f"Global robot shipments {year}",
            "series": "Global annual humanoid robot shipments",
            "value": value,
            "unit": "robots/year",
            "as_of_date": "2026-07-01",
            "period_end": f"{year}-12-31",
            "value_type": "observed" if year <= 2025 else "forecast",
            "geography": "Global",
            "technology_route": "All routes",
            "definition": "Calendar-year worldwide commercial humanoid robot shipments",
            "source": "Industry shipment dataset",
            "source_url": "https://example.test/shipments",
            "tier": "S2",
        }
        for year, value in ((2024, 1200), (2025, 3500), (2027, 18000), (2030, 120000))
    ]

    prepared = renderer.prep_trajectories(rows)

    assert prepared is not None
    points = prepared["series"][0]["points"]
    assert [point["period_end"] for point in points] == [
        "2024-12-31", "2025-12-31", "2027-12-31", "2030-12-31"
    ]
    assert [point["projection"] for point in points] == [False, False, True, True]
    assert {point["as_of"] for point in points} == {"2026-07-01"}


def test_prep_trajectories_uses_nested_period_end_before_publication_date():
    renderer = _load_renderer()
    rows = [
        {
            "metric": f"Global robot shipments {year}",
            "series": "Global annual humanoid robot shipments",
            "value_num": value,
            "unit": "robots/year",
            "as_of_date": "2026-07-01",
            "period": {"end": f"{year}-12-31"},
            "value_kind": "actual" if year <= 2025 else "forecast",
            "definition": "Calendar-year worldwide commercial humanoid robot shipments",
            "source": "Industry shipment dataset",
            "source_url": "https://example.test/shipments",
        }
        for year, value in ((2024, 1200), (2025, 3500), (2030, 120000))
    ]

    prepared = renderer.prep_trajectories(rows)

    assert prepared is not None
    points = prepared["series"][0]["points"]
    assert [point["year"] for point in points] == [2024, 2025, 2030]
    assert [point["period_end"] for point in points] == [
        "2024-12-31", "2025-12-31", "2030-12-31",
    ]


def test_prep_trajectories_requires_three_periods_and_published_projection_provenance():
    renderer = _load_renderer()
    base = {
        "series": "Global storage deployment",
        "metric_family": "cumulative deployment",
        "unit": "GW",
        "region": "Global",
        "definition": "Cumulative operating grid-scale storage power",
        "as_of_date": "2026-07-01",
    }
    two_rows = [
        {**base, "metric": f"Deployment {year}", "value_num": value, "year": year,
         "period_end": f"{year}-12-31", "value_kind": "actual", "source": "Agency"}
        for year, value in ((2024, 100), (2025, 150))
    ]
    assert renderer.prep_trajectories(two_rows) is None

    internal = two_rows + [{
        **base,
        "metric": "Deployment 2030",
        "value_num": 900,
        "year": 2030,
        "period_end": "2030-12-31",
        "value_kind": "forecast",
        "source": "Global dossier analyst interpolation",
    }]
    assert renderer.prep_trajectories(internal) is None

    published = [dict(row) for row in internal]
    published[-1]["source"] = "Published industry outlook"
    published[-1]["source_url"] = "https://example.test/outlook"
    assert renderer.prep_trajectories(published) is not None

    for invalid_url in (
        "not-a-url", "file:///tmp/outlook", "javascript:alert(1)",
        "https:///missing-host",
    ):
        invalid = [dict(row) for row in published]
        invalid[-1]["source_url"] = invalid_url
        assert renderer.prep_trajectories(invalid) is None


def test_timeline_display_text_neutralizes_plotly_math_delimiters():
    renderer = _load_renderer()
    text = renderer._plotly_text(
        "Revenue fell from US$192/kW-year to US$55/kW-year."
    )
    assert "$" not in text
    assert "USD 192/kW-year" in text
    assert "USD 55/kW-year" in text


def test_prep_trajectories_rejects_rows_without_explicit_period_end():
    renderer = _load_renderer()
    rows = [
        {
            "metric": f"Battery cost {year}",
            "series": "Battery cost",
            "value": value,
            "unit": "USD/kWh",
            "as_of_date": f"{year}-12-31",
            "value_type": "observed",
            "definition": "Volume-weighted battery pack cost",
            "source": "Published battery survey",
        }
        for year, value in ((2023, 140), (2024, 115), (2025, 108))
    ]

    assert renderer.prep_trajectories(rows) is None


def _bnef_revision_rows():
    return [
        {"metric": "BNEF US 2030 EV share projection (2024)", "value": 48,
         "unit": "% of US new car sales", "as_of_date": "2024-12-31",
         "definition": "BNEF 2024 forecast for US 2030 EV share",
         "source": "BNEF EVO 2024"},
        {"metric": "BNEF US 2030 EV share projection (2025)", "value": 27,
         "unit": "% of US new car sales", "as_of_date": "2025-12-31",
         "definition": "BNEF 2025 revision for US 2030 EV share",
         "source": "BNEF EVO 2026 [S10]"},
        {"metric": "BNEF US 2030 EV share projection (2026)", "value": 17,
         "unit": "% of US new car sales", "as_of_date": "2026-06-30",
         "definition": "BNEF 2026 revision for US 2030 EV share post-IRA repeal",
         "source": "BNEF EVO 2026 [S10]"},
    ]


def test_source_outlook_family_normalizes_bnef_aliases_citations_and_recap_years():
    renderer = _load_renderer()
    assert renderer._source_outlook_family(
        "BloombergNEF's Electric Vehicle Outlook 2024 [S4]"
    ) == "bnef evo"
    assert renderer._source_outlook_family("BNEF EVO 2026 [S10] recap") == "bnef evo"
    assert renderer._source_outlook_family("IEA Global EV Outlook 2026") != "bnef evo"


def test_prep_revisions_requires_three_matching_forecast_vintages():
    renderer = _load_renderer()
    prepared = renderer.prep_revisions(_bnef_revision_rows() + [
        {"metric": "Unrelated actual (2026)", "value": 99, "unit": "%"},
    ])

    assert prepared is not None
    assert prepared["series"][0]["name"] == "BNEF US 2030 EV share projection"
    assert prepared["series"][0]["publisher_family"] == "bnef evo"
    assert prepared["series"][0]["target_year"] == 2030
    assert [point["value"] for point in prepared["series"][0]["points"]] == [48, 27, 17]


def test_prep_revisions_rejects_cross_publisher_and_definition_drift():
    renderer = _load_renderer()
    cross_publisher = _bnef_revision_rows()
    cross_publisher[1] = {
        **cross_publisher[1],
        "source": "IEA Global EV Outlook 2025",
    }
    assert renderer.prep_revisions(cross_publisher) is None

    definition_drift = _bnef_revision_rows()
    definition_drift[2] = {
        **definition_drift[2],
        "definition": "BNEF 2026 forecast for US 2030 BEV-only share",
    }
    assert renderer.prep_revisions(definition_drift) is None

    unit_drift = _bnef_revision_rows()
    unit_drift[2] = {**unit_drift[2], "unit": "% of total US vehicle fleet"}
    assert renderer.prep_revisions(unit_drift) is None


def test_prep_revisions_does_not_treat_target_year_as_vintage():
    renderer = _load_renderer()
    rows = [
        {"metric": "US EV share projection (2028)", "value": 24,
         "unit": "% new car sales", "as_of_date": "2024-12-31",
         "definition": "BNEF forecast for US 2028 EV share", "source": "BNEF EVO 2024"},
        {"metric": "US EV share projection (2029)", "value": 31,
         "unit": "% new car sales", "as_of_date": "2025-12-31",
         "definition": "BNEF forecast for US 2029 EV share", "source": "BNEF EVO 2025"},
        {"metric": "US EV share projection (2030)", "value": 37,
         "unit": "% new car sales", "as_of_date": "2026-06-30",
         "definition": "BNEF forecast for US 2030 EV share", "source": "BNEF EVO 2026"},
    ]

    assert renderer.prep_revisions(rows) is None


def test_prep_network_uses_declared_tiers_and_canonicalizes_relationship_endpoints():
    renderer = _load_renderer()
    prepared = renderer.prep_network({
        "actors": [
            {"name": "Toyota", "influence": "high",
             "salience": {"tier": "high", "basis": "Launch expected 2027-2028"}},
            {"name": "CATL (Contemporary Amperex Technology)", "influence": "high",
             "salience": {"tier": "high", "basis": "55.6% combined share"}},
            {"name": "Regulator", "influence": "medium", "salience": "medium"},
        ],
        "relationships": [
            {"source": "CATL", "target": "Toyota", "type": "SUPPLIES",
             "valence": "allied", "polarity": 0.8},
            {"source": "Regulator", "target": "CATL", "type": "REGULATES",
             "valence": "directional", "polarity": 0.1},
        ],
    })

    assert prepared is not None
    nodes = {node["name"]: node for node in prepared["nodes"]}
    assert nodes["Toyota"]["size"] == nodes["CATL (Contemporary Amperex Technology)"]["size"]
    assert nodes["Toyota"]["size"] > nodes["Regulator"]["size"]
    assert len(prepared["edges"]) == 2
    assert prepared["edges"][0]["color"] == renderer._EDGE_COLORS["cooperative"]


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

    class FakeSpine:
        def set_color(self, *args, **kwargs):
            return None

    class FakePatch:
        def set_facecolor(self, *args, **kwargs):
            return None

    class FakeAxes:
        def __init__(self):
            self.annotations = []
            self.spines = {"left": FakeSpine(), "bottom": FakeSpine()}

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

        def set_facecolor(self, *args, **kwargs):
            return None

        def tick_params(self, *args, **kwargs):
            return None

    class FakeFigure:
        # Mirrors the real matplotlib Figure surface the renderer's WAVE9
        # finalizer touches: patch/get_axes for the surface color, savefig
        # with a facecolor kwarg.
        def __init__(self, axes):
            self.patch = FakePatch()
            self._axes = [axes]

        def get_axes(self):
            return self._axes

        def tight_layout(self):
            return None

        def savefig(self, path, **kwargs):
            Path(path).touch()

    class FakePyplot:
        def __init__(self):
            self.axes = FakeAxes()

        def subplots(self, *args, **kwargs):
            return FakeFigure(self.axes), self.axes

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
    assert [point["index"] for point in prepared["points"]] == [1, 2, 3]

    png_path, html_path = renderer.render_timeline(prepared, charts_dir)

    assert png_path == "charts/timeline.png"
    assert html_path is None
    assert len(fake_plt.axes.annotations) == 3


def test_timeline_plotly_key_keeps_every_event(tmp_path, monkeypatch):
    renderer = _load_renderer()
    if not renderer._HAS_PLOTLY:
        pytest.skip("plotly not installed")
    captured = {}
    monkeypatch.setattr(
        renderer,
        "_write_outputs",
        lambda fig, _mpl, _charts, _stem: captured.setdefault("fig", fig) or (None, None),
    )
    prepared = renderer.prep_timeline([
        {"date": f"2026-{month:02d}-{day:02d}",
         "event": f"Milestone {month:02d}-{day:02d}"}
        for month in range(1, 3)
        for day in range(1, 14)
    ])

    renderer.render_timeline(prepared, tmp_path)

    annotations = list(captured["fig"].layout.annotations)
    assert len(annotations) == len(prepared["points"]) == 26
    assert annotations[-1].text.startswith("<b>26</b>")


def test_timeline_duplicate_dates_do_not_overlap_on_same_lane():
    renderer = _load_renderer()
    prepared = renderer.prep_timeline(
        [
            {"date": "2026-06-30", "event": "Supply shock"},
            {"date": "2026-06-30", "event": "Policy response"},
        ]
    )

    points = prepared["points"]
    assert {point["date"] for point in points} == {"2026-06-30"}
    assert len({point["lane"] for point in points}) == 2


def test_timeline_nearby_dates_four_positions_apart_use_different_lanes():
    renderer = _load_renderer()
    prepared = renderer.prep_timeline(
        [
            {"date": "2026-06-30", "event": "Event 1"},
            {"date": "2026-07-02", "event": "Event 2"},
            {"date": "2026-07-05", "event": "Event 3"},
            {"date": "2026-07-08", "event": "Event 4"},
            {"date": "2026-07-11", "event": "Event 5"},
        ]
    )

    points = prepared["points"]
    assert points[0]["date"] == "2026-06-30"
    assert points[4]["date"] == "2026-07-11"
    assert points[0]["lane"] != points[4]["lane"]


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
    # source_quality is a diagnostic figure and is off by default; opt in explicitly
    # so this test still exercises the source path under the new diagnostics gate.
    monkeypatch.setattr(
        sys, "argv", [str(RENDERER_PATH), "--dir", str(tmp_path), "--diagnostics"]
    )

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
    assert b"<title>" in first
    assert b'data:image/svg+xml' in first
    assert all(line == line.rstrip(b" \t") for line in first.splitlines())
