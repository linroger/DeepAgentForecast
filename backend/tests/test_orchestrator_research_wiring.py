"""Offline unit tests for orchestrator research-stage wiring.

Covers the pure helpers landed for R2-RES-7 (as_of anchor validation) and
R2-RES-3 (advisory forecast-confidence penalty). No network / LLM / disk.
"""

from datetime import datetime, timedelta, timezone

from app.services.pipeline_orchestrator import PipelineOrchestrator


# ── R2-RES-7: as_of anchor validation ───────────────────────────────────────

def _src(date_str):
    return {"title": "x", "url": "u", "tier": "S1", "date": date_str}


def test_as_of_valid_within_bounds_is_kept():
    actors = {"as_of_date": "2026-05-01"}
    sources = [_src("2026-01-01"), _src("2026-04-15")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is None
    assert dt is not None and dt.date().isoformat() == "2026-05-01"


def test_as_of_future_falls_back_to_max_source():
    future = (datetime.now(timezone.utc) + timedelta(days=400)).date().isoformat()
    actors = {"as_of_date": future}
    sources = [_src("2026-02-01"), _src("2026-03-20")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "晚于运行日" in note
    assert dt is not None and dt.date().isoformat() == "2026-03-20"


def test_as_of_predates_evidence_falls_back():
    actors = {"as_of_date": "2024-01-01"}
    sources = [_src("2026-02-01"), _src("2026-03-20")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "早于最新来源日" in note
    assert dt is not None and dt.date().isoformat() == "2026-03-20"


def test_as_of_unparseable_with_sources_falls_back():
    actors = {"as_of_date": "sometime last spring"}
    sources = [_src("2026-02-01")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "无法解析" in note
    assert dt is not None and dt.date().isoformat() == "2026-02-01"


def test_as_of_absent_no_sources_preserves_none():
    # degrade-safe: no anchor available → byte-identical to today's None behavior.
    dt, note = PipelineOrchestrator._validate_as_of_date({}, None)
    assert dt is None and note is None
    dt2, note2 = PipelineOrchestrator._validate_as_of_date(None, [])
    assert dt2 is None and note2 is None


def test_as_of_future_no_sources_uses_run_date():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    dt, note = PipelineOrchestrator._validate_as_of_date({"as_of_date": future}, None)
    assert note is not None
    assert dt is not None and dt.date() == datetime.now(timezone.utc).date()


def test_as_of_garbage_inputs_never_raise():
    # tolerate any shape of dirty data
    for actors, sources in [(123, "nope"), ([], {}), ("x", [{"date": None}, 5])]:
        dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
        assert dt is None or hasattr(dt, "date")


# ── R2-RES-3: advisory forecast-confidence penalty ──────────────────────────

def test_penalty_zero_when_no_signals():
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(None, None)
    assert pen == 0.0 and comp == {}


def test_penalty_from_low_research_quality():
    # Config.RESEARCH_QUALITY_FLOOR default 0.45; score 0.30 → 0.15 (capped).
    meta = {"research_quality": {"score": 0.30}}
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, None)
    assert comp.get("research_quality") == 0.15
    assert pen >= 0.15


def test_penalty_from_low_tier_sources():
    meta = {"source_tiers": {"S3": 4}}  # all low tier (weight 0.4) → (1-0.4)*0.1=0.06
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, None)
    assert comp.get("source_tier_mix") == 0.06
    # high-tier sources should yield no penalty
    pen2, comp2 = PipelineOrchestrator._compute_forecast_confidence_penalty(
        {"source_tiers": {"S1": 5}}, None)
    assert "source_tier_mix" not in comp2 and pen2 == 0.0


def test_penalty_from_weak_dossier_coverage():
    cov = {"n_actors": 5, "pct_actors_with_incentives": 0.1,
           "n_relationships": 3, "pct_edges_valenced": 0.0}
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(None, cov)
    assert comp.get("dossier_coverage") == 0.1  # two weak signals × 0.05
    assert pen == 0.1


def test_penalty_capped_at_0_3():
    meta = {"research_quality": {"score": 0.0}, "source_tiers": {"S3": 10}}
    cov = {"n_actors": 5, "pct_actors_with_incentives": 0.0,
           "n_relationships": 0}
    pen, _ = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, cov)
    assert pen <= 0.3
