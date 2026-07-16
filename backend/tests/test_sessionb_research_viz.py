"""SESSION-B: research-report quant enrichment + trajectory rendering + diagnostic gating.

Covers the four acceptance checks for the DEEP-RESEARCH visualization fixes:

  (a) the deterministic enrichment post-processor turns the *real* run's
      quantitative.json into trustworthy canonical metric families and repairs
      model-supplied family labels that conflict with the row itself;
  (b) render.prep_trajectories emits only sourced, three-period series and
      rejects internal dossier interpolations masquerading as published outlooks;
  (c) source_quality is a diagnostic — never in the decision/required contract,
      absent from the default renderer jobs, and skipped by embed_chart_refs;
  (d) legacy rows carrying no canonical fields still group via the text fallback.

Both modules are stdlib-only at import time (heavy deps live inside functions),
so they load via importlib without running main(). Plotly/kaleido are optional
and never exercised here — every check runs on the pure prep/enrich functions.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PY = REPO / "deerflow_bridge" / "deerflow_research.py"
RENDERER_PY = (
    REPO / "deerflow_bridge" / "skills" / "forecast-visuals" / "scripts" / "render.py"
)
REAL_QUANT = (
    REPO / "backend" / "uploads" / "pipelines" / "pipe_bef6879b2e94"
    / "handoff" / "quantitative.json"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bridge():
    return _load("deerflow_research_sessionb", BRIDGE_PY)


@pytest.fixture(scope="module")
def renderer():
    return _load("forecast_visuals_sessionb", RENDERER_PY)


def _real_rows():
    if not REAL_QUANT.exists():
        pytest.skip(f"real quant fixture missing: {REAL_QUANT}")
    rows = json.loads(REAL_QUANT.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and rows
    return rows


# ---------------------------------------------------------------------------
# (a) enrichment folds the real, per-row-unique data into groupable families
# ---------------------------------------------------------------------------

def test_enrichment_groups_real_quant_into_family_with_two_year_points(bridge):
    rows = _real_rows()
    enriched = bridge.enrich_quantitative_rows([dict(r) for r in rows])

    # every derivable row must now carry canonical grouping fields
    assert any(r.get("metric_family") for r in enriched)
    assert any(isinstance(r.get("value_num"), float) for r in enriched)
    assert any(isinstance(r.get("year"), int) for r in enriched)
    assert {r.get("value_kind") for r in enriched} >= {"actual", "forecast"}

    # group by (metric_family, region) and demand at least one family spanning
    # >=2 distinct years — the precondition for a trajectory line.
    groups: dict[tuple[str, str], set[int]] = {}
    for r in enriched:
        fam, year = r.get("metric_family"), r.get("year")
        if fam and isinstance(year, int):
            groups.setdefault((fam, r.get("region") or "Global"), set()).add(year)
    multiyear = {key: yrs for key, yrs in groups.items() if len(yrs) >= 2}
    assert multiyear, f"no multi-year family emerged: {groups}"

    # the humanoid shipment forecasts are the canonical example.
    shipment_families = {
        key for key in multiyear if "shipment" in key[0]
    }
    assert shipment_families, f"expected an 'annual shipments' family: {multiyear}"


def test_enrichment_value_num_handles_operators_ranges_and_currency(bridge):
    fn = bridge._quant_value_num
    assert fn(">250000") == 250000.0
    assert fn("446000") == 446000.0
    assert fn("13-17") == 15.0            # range -> midpoint
    assert fn("40-60 % of BOM") == 50.0   # range with trailing unit words
    assert fn("<12 months") == 12.0
    assert fn("~$54B") == 54.0
    assert fn("2.001") == 2.001
    assert fn("0.125") == 0.125
    assert fn("2,001") == 2001.0          # thousands separator stripped
    assert fn("") is None
    assert fn("n/a") is None


def test_enrichment_preserves_evidence_fields_but_corrects_derived_family(bridge):
    row = {
        "metric": "custom shipments 2030",
        "value": "500000",
        "unit": "units",
        "period_end": "2030-12-31",
        "value_type": "forecast",
        "geography": "China",
        # Evidence fields survive untouched. metric_family is derived display
        # metadata, so a conflicting model label must be corrected and audited.
        "metric_family": "bespoke family",
        "value_num": 999.0,
        "year": 1999,
        "region": "Narnia",
        "value_kind": "actual",
    }
    (out,) = bridge.enrich_quantitative_rows([row])
    assert out["metric_family"] == "annual shipments"
    assert out["metric_family_claimed"] == "bespoke family"
    assert out["value_num"] == 999.0
    assert out["year"] == 1999
    assert out["region"] == "Narnia"
    assert out["value_kind"] == "actual"


def test_enrichment_prefers_nested_target_period_over_publication_date(bridge):
    row = {
        "metric": "2030 global robot shipments",
        "series": "Global annual humanoid robot shipments",
        "value": "120000",
        "unit": "robots/year",
        "as_of_date": "2026-07-01",
        "period": {"end": "2030-12-31"},
        "value_type": "forecast",
    }

    (out,) = bridge.enrich_quantitative_rows([row])

    assert out["year"] == 2030


@pytest.mark.parametrize(
    ("metric", "series", "unit"),
    [
        ("Data-center power requirement forecast", "Base case forecast", "GW"),
        ("Global grid deployment outlook", "Robots and automation outlook", "GW"),
        ("2030 forecast", "Base case", "USD billion"),
    ],
)
def test_generic_forecast_wording_is_not_mislabeled_as_shipments(
        bridge, metric, series, unit):
    row = {
        "metric": metric,
        "series": series,
        "value": "100",
        "unit": unit,
        "definition": metric,
    }

    (out,) = bridge.enrich_quantitative_rows([row])

    assert out.get("metric_family") != "annual shipments"


@pytest.mark.parametrize(
    ("metric", "series", "unit", "claimed", "expected"),
    [
        ("Global grid-scale cumulative storage power working trajectory",
         "Dossier global grid-scale cumulative storage power", "TW", "market share",
         "cumulative deployment"),
        ("Global annual battery additions working interpolation",
         "Dossier global annual battery additions interpolation", "GW/year", "annual shipments",
         "annual installations"),
        ("Global battery-storage average duration working assumption",
         "Dossier global battery-storage average duration", "hours", "cumulative deployment",
         "duration"),
        ("2030 intra-day LDES power-capex target", "LDES intra-day power capex",
         "USD/kW", "annual installations", "power capex"),
        ("2030 multi-day LDES round-trip-efficiency target",
         "LDES multi-day round-trip efficiency", "%", "growth rate",
         "round-trip efficiency"),
        ("Lithium Dominance scenario probability",
         "Dossier 2040 global storage scenario probability", "% probability", "annual revenue",
         "scenario probability"),
    ],
)
def test_enrichment_repairs_live_grid_family_misclassifications(
    bridge, metric, series, unit, claimed, expected,
):
    row = {
        "metric": metric,
        "series": series,
        "value": "25",
        "unit": unit,
        "definition": metric,
        "metric_family": claimed,
    }
    (out,) = bridge.enrich_quantitative_rows([row])
    assert out["metric_family"] == expected
    assert out["metric_family_claimed"] == claimed


# ---------------------------------------------------------------------------
# (b) render.prep_trajectories renders series from the enriched real data
# ---------------------------------------------------------------------------

def test_prep_trajectories_never_emits_weak_series_from_enriched_real_quant(
    bridge, renderer,
):
    rows = bridge.enrich_quantitative_rows([dict(r) for r in _real_rows()])
    prepared = renderer.prep_trajectories(rows)

    # A run may legitimately have no eligible trajectory. If one is emitted,
    # every line must meet the publication contract by itself.
    for series in (prepared or {}).get("series", []):
        assert len({p["year"] for p in series["points"]}) >= 3
        assert all(p["source"] and p["as_of"] and p["definition"] for p in series["points"])
        assert all("dossier analyst" not in p["source"].casefold() for p in series["points"])


def test_prep_trajectories_rejects_internal_dossier_interpolation(renderer):
    rows = [
        {
            "metric": f"Global storage working interpolation {year}",
            "series": "Dossier global storage working interpolation",
            "metric_family": "cumulative deployment",
            "value_num": value,
            "year": year,
            "unit": "TW",
            "region": "Global",
            "value_kind": "forecast",
            "period_end": f"{year}-12-31",
            "as_of_date": "2026-07-15",
            "definition": "Internal analyst interpolation for the dossier",
            "source": "Global dossier analyst interpolation",
        }
        for year, value in ((2030, 1.0), (2035, 2.1), (2040, 3.4))
    ]
    assert renderer.prep_trajectories(rows) is None


def test_prep_trajectories_dedupes_by_year_keeping_best_tier(renderer):
    rows = [
        {"metric_family": "annual shipments", "value_num": 100.0, "year": 2025,
         "unit": "units", "region": "Global", "value_kind": "actual",
         "period_end": "2025-12-31", "as_of_date": "2026-01-01",
         "definition": "Calendar-year worldwide shipments", "source": "Agency",
         "tier": "S3"},
        {"metric_family": "annual shipments", "value_num": 130.0, "year": 2025,
         "unit": "units", "region": "Global", "value_kind": "actual",
         "period_end": "2025-12-31", "as_of_date": "2026-01-01",
         "definition": "Calendar-year worldwide shipments", "source": "Agency",
         "tier": "S1"},
        {"metric_family": "annual shipments", "value_num": 900.0, "year": 2030,
         "unit": "units", "region": "Global", "value_kind": "forecast",
         "period_end": "2030-12-31", "as_of_date": "2026-01-01",
         "definition": "Calendar-year worldwide shipments", "source": "Published outlook",
         "source_url": "https://example.test/outlook", "tier": "S2"},
        {"metric_family": "annual shipments", "value_num": 1500.0, "year": 2035,
         "unit": "units", "region": "Global", "value_kind": "forecast",
         "period_end": "2035-12-31", "as_of_date": "2026-01-01",
         "definition": "Calendar-year worldwide shipments", "source": "Published outlook",
         "source_url": "https://example.test/outlook", "tier": "S2"},
    ]
    prepared = renderer.prep_trajectories(rows)
    assert prepared is not None
    (series,) = prepared["series"]
    points = {p["year"]: p for p in series["points"]}
    assert set(points) == {2025, 2030, 2035}
    assert points[2025]["value"] == 130.0  # S1 beat S3 for the same year
    assert points[2025]["projection"] is False
    assert points[2030]["projection"] is True


# ---------------------------------------------------------------------------
# (c) source_quality is diagnostic-only, everywhere
# ---------------------------------------------------------------------------

def test_source_quality_absent_from_decision_and_required_contract(bridge):
    assert "source_quality" not in bridge._DECISION_VISUAL_IDS
    # No question phrasing may pull source_quality into the required set.
    for prompt in (
        "Show cost curves, deployment trajectories, regional comparisons, "
        "forecast revisions, prediction-market probabilities, and source quality "
        "diagnostics with full visualizations.",
        "可视化 图表 source quality freshness diagnostics",
    ):
        _explicit, required = bridge._visual_contract_requirements(prompt)
        assert "source_quality" not in required


def test_source_quality_off_by_default_in_renderer_jobs(renderer, tmp_path, monkeypatch):
    # A sources.json alone must not produce a source_quality chart by default.
    (tmp_path / "sources.json").write_text(
        json.dumps([{"url": "https://example.com", "tier": "S1", "date": "2026-07-10"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(renderer, "_HAS_MPL", True)
    monkeypatch.setattr(
        renderer, "render_sources",
        lambda prep, charts_dir: ("charts/source_quality.png", "charts/source_quality.html"),
    )
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", [str(RENDERER_PY), "--dir", str(tmp_path)])

    # default (no --diagnostics): no eligible artifact => exit 2, no source_quality
    assert renderer.main() == 2
    assert not (tmp_path / "charts.json").exists()

    # opt-in via env var: source_quality is rendered and registered
    monkeypatch.setenv("RESEARCH_CHARTS_INCLUDE_DIAGNOSTICS", "1")
    assert renderer.main() == 0
    manifest = json.loads((tmp_path / "charts.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in manifest] == ["source_quality"]


def test_embed_chart_refs_skips_diagnostic_charts(bridge):
    report = "# Report\n\nBody paragraph.\n"
    charts = [
        {"id": "metric_trajectories", "title": "Trajectories",
         "path": "charts/metric_trajectories.png", "caption": "traj",
         "data_class": "forecast_domain"},
        {"id": "source_quality", "title": "Source Quality",
         "path": "charts/source_quality.png", "caption": "diag",
         "data_class": "diagnostic"},
        {"id": "custom_audit", "title": "Audit",
         "path": "charts/custom_audit.png", "data_class": "diagnostic"},
    ]
    out = bridge.embed_chart_refs(report, charts)
    assert "charts/metric_trajectories.png" in out
    assert "## Visual Annex" in out
    # diagnostic charts (by id OR by data_class) never reach the body
    assert "source_quality" not in out
    assert "charts/custom_audit.png" not in out


# ---------------------------------------------------------------------------
# (d) legacy rows without canonical fields still group via the text fallback
# ---------------------------------------------------------------------------

def test_legacy_rows_group_via_series_text_fallback(renderer):
    # No metric_family / value_num / year / region / value_kind — pure legacy shape,
    # but a shared series name folds and structured period_end supplies the year.
    rows = [
        {
            "metric": f"Battery pack price {year}",
            "series": "Battery pack price",
            "value": f"{value}",
            "unit": "USD/kWh",
            "as_of_date": "2026-07-01",
            "period_end": f"{year}-12-31",
            "value_type": "observed",
            "definition": "Volume-weighted battery pack price",
            "source": "BNEF survey",
        }
        for year, value in ((2023, 140), (2024, 115), (2025, 108))
    ]
    prepared = renderer.prep_trajectories(rows)
    assert prepared is not None
    (series,) = prepared["series"]
    assert [p["period_end"] for p in series["points"]] == [
        "2023-12-31", "2024-12-31", "2025-12-31"
    ]
    assert all(p["projection"] is False for p in series["points"])


def test_legacy_forecast_rows_group_via_text_year_fallback(renderer):
    # Published forecast rows with the target year only in text (no period_end):
    # the fallback may read target years, but still needs three periods and
    # external publication provenance.
    rows = [
        {"metric": f"Deployment forecast {year}", "series": "Fleet deployment",
         "value": str(value), "unit": "units", "value_type": "forecast",
         "definition": "Installed fleet", "source": "Published Outlook",
         "source_url": "https://example.test/outlook", "as_of_date": "2026-06-30"}
        for year, value in ((2027, 5000), (2030, 40000), (2035, 150000))
    ]
    prepared = renderer.prep_trajectories(rows)
    assert prepared is not None
    (series,) = prepared["series"]
    assert {p["year"] for p in series["points"]} == {2027, 2030, 2035}
    assert all(p["projection"] is True for p in series["points"])
