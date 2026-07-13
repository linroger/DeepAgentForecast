"""Focused regression coverage for LOOP-008 live research observability."""

from __future__ import annotations

import json

import pytest
from flask import Flask

from app.api import research_bp
from app.services.pipeline_orchestrator import PipelineManager
from app.services.research_progress import (
    ResearchProgressEstimator,
    aggregate_parallel_progress,
    merged_research_progress_full,
    merged_research_progress_tail,
)


def test_phase_bounded_progress_does_not_jump_to_ninety_during_opening_tools():
    estimator = ResearchProgressEstimator()
    observed = [estimator.observe("2026-01-01T00:00:00+00:00 [init] client ready")]
    observed.append(estimator.observe(
        "2026-01-01T00:00:01+00:00 [stage] deep: starting multi-pass research protocol"))
    observed.append(estimator.observe(
        "2026-01-01T00:00:02+00:00 [stage] research:deep-opening: starting agent turn"))
    for i in range(300):
        kind = "tool" if i % 2 == 0 else "result"
        observed.append(estimator.observe(f"2026-01-01T00:00:03+00:00 [{kind}] event {i}"))

    # Hundreds of opening events remain inside the opening phase's 8..16 band.
    assert observed == sorted(observed)
    assert estimator.progress < 16
    opening_progress = estimator.progress
    assert estimator.observe(
        "2026-01-01T00:05:00+00:00 [stage] actor-ontology synthesize: produced 70000 chars"
    ) == opening_progress
    assert estimator.observe(
        "2026-01-01T00:05:01+00:00 [result] article says research complete; "
        "wrote research_report.md"
    ) < 16
    assert estimator.observe(
        "2026-01-01T00:05:02+00:00 [result] docs literally say [done] research complete"
    ) < 16
    assert estimator.observe(
        '2026-01-01T00:05:03+00:00 [tool] web_search(query="[stage] wrote sources.json")'
    ) < 16

    assert estimator.observe(
        "2026-01-01T00:10:00+00:00 [stage] deep fan-out: 8 scoped workers") == 18
    for i in range(500):
        estimator.observe(f"2026-01-01T00:10:01+00:00 [tool] fanout {i}")
    assert estimator.progress < 32

    assert estimator.observe(
        "2026-01-01T01:00:00+00:00 [stage] deep: running 3 middle phases in parallel") == 42
    assert estimator.observe(
        "2026-01-01T02:00:00+00:00 [stage] synthesize/multipart: requesting section outline") == 78
    assert estimator.observe(
        "2026-01-01T03:00:00+00:00 [done] research complete") == 99
    # Late/out-of-order stdout cannot regress the UI.
    assert estimator.observe("2026-01-01T00:00:00+00:00 [init] client ready") == 99


def test_parallel_progress_is_equal_weight_monotonic_and_reserves_merge_band():
    assert aggregate_parallel_progress([20, 40, 60], previous=2) == 40
    assert aggregate_parallel_progress([100, 100, 100], previous=40) == 95
    assert aggregate_parallel_progress([10, 10, 10], previous=41) == 41
    assert aggregate_parallel_progress([], previous=7) == 7
    # Once a failed lane is terminal and excluded, surviving lanes can reach the
    # reserved merge boundary instead of jumping from ~64 straight to 96.
    assert aggregate_parallel_progress(
        [95, 95, 2], previous=64, excluded_indices={2}) == 95


def test_report_refinement_does_not_impersonate_late_triangulation():
    estimator = ResearchProgressEstimator()
    assert estimator.observe(
        "2026-01-01T00:00:00+00:00 [stage] research-report refine round 1: addressing 3 gaps"
    ) == 88
    assert estimator.observe(
        "2026-01-01T00:10:00+00:00 [ok] research-report refine round 1: "
        "adopted re-synthesized report (100000 chars)"
    ) == 88
    assert estimator.observe(
        "2026-01-01T00:20:00+00:00 [stage] triangulation top-up: verifying 5 claims"
    ) == 95


def test_deep_coverage_topups_advance_in_bounded_slots_before_synthesis():
    estimator = ResearchProgressEstimator()
    assert estimator.observe(
        "2026-01-01T00:00:00+00:00 [stage] research:deep-5-forecast-implications: "
        "turn complete (80 tool calls, 2000 chars)"
    ) == 68
    observed = []
    for number in range(1, 5):
        observed.append(estimator.observe(
            f"2026-01-01T00:0{number}:00+00:00 [stage] "
            f"research:deep-coverage-topup-{number}: starting agent turn"))
        for i in range(80):
            estimator.observe(
                f"2026-01-01T00:0{number}:01+00:00 "
                f"[tool] coverage round {number} event {i}")
        observed.append(estimator.observe(
            f"2026-01-01T00:0{number}:59+00:00 [stage] "
            f"research:deep-coverage-topup-{number}: turn complete"))

    assert observed == sorted(observed)
    assert observed[0] == 69
    assert observed[-1] == 76
    assert max(observed) < 78
    assert estimator.observe(
        "2026-01-01T00:10:00+00:00 [stage] synthesize/multipart: requesting section outline"
    ) == 78


def test_merged_tail_reads_active_tracks_orders_deduplicates_and_bounds(tmp_path):
    track1 = tmp_path / "track_1"
    track2 = tmp_path / "track_2"
    ignored = tmp_path / "track_bad"
    track1.mkdir()
    track2.mkdir()
    ignored.mkdir()
    first = "2026-01-01T00:00:01+00:00 [tool] first"
    (track1 / "research_progress.log").write_text(
        first + "\n" + first + "\n2026-01-01T00:00:04+00:00 [result] fourth\n",
        encoding="utf-8",
    )
    (track2 / "research_progress.log").write_text(
        "2026-01-01T00:00:02+00:00 [stage] second\n"
        "2026-01-01T00:00:03+00:00 [ok] third\n",
        encoding="utf-8",
    )
    (ignored / "research_progress.log").write_text(
        "2026-01-01T00:00:05+00:00 [error] must not leak\n", encoding="utf-8")

    tail = merged_research_progress_tail(str(tmp_path), limit=3)

    assert tail.source_count == 2
    assert tail.truncated is True
    assert tail.lines == [
        "2026-01-01T00:00:02+00:00 [track:2] [stage] second",
        "2026-01-01T00:00:03+00:00 [track:2] [ok] third",
        "2026-01-01T00:00:04+00:00 [track:1] [result] fourth",
    ]
    assert all("must not leak" not in line for line in tail.lines)


def test_multiline_continuation_keeps_source_order_instead_of_masking_live_tail(tmp_path):
    track = tmp_path / "track_1"
    track.mkdir()
    (track / "research_progress.log").write_text(
        "2026-01-01T00:00:01+00:00 [result] first line\n"
        "continuation of first line\n"
        "2026-01-01T00:00:02+00:00 [tool] genuinely newest\n",
        encoding="utf-8",
    )

    tail = merged_research_progress_tail(str(tmp_path), limit=10)

    assert tail.lines == [
        "2026-01-01T00:00:01+00:00 [track:1] [result] first line",
        "[track:1] continuation of first line",
        "2026-01-01T00:00:02+00:00 [track:1] [tool] genuinely newest",
    ]


def test_full_progress_snapshot_preserves_early_rows_duplicates_and_exact_total(tmp_path):
    root = tmp_path / "research_progress.log"
    track = tmp_path / "track_1"
    track.mkdir()
    root.write_text(
        "2026-01-01T00:00:03+00:00 [done] root summary\n",
        encoding="utf-8",
    )
    repeated = "2026-01-01T00:00:01+00:00 [tool] repeated"
    (track / "research_progress.log").write_text(
        repeated + "\n" + repeated + "\n"
        "2026-01-01T00:00:02+00:00 [result] later\n",
        encoding="utf-8",
    )

    snapshot = merged_research_progress_full(str(tmp_path))

    assert snapshot.source_count == 2
    assert snapshot.truncated is False
    assert len(snapshot.lines) == 4
    assert snapshot.lines[:2] == [
        "2026-01-01T00:00:01+00:00 [track:1] [tool] repeated",
        "2026-01-01T00:00:01+00:00 [track:1] [tool] repeated",
    ]
    assert snapshot.lines[-1].endswith("[done] root summary")


def test_full_progress_snapshot_includes_preserved_failed_attempt_logs(tmp_path):
    attempts = tmp_path / "research_attempts"
    attempts.mkdir()
    (attempts / "global_synthesis_1_failed_abc123.log").write_text(
        "2026-01-01T00:00:00+00:00 [error] first synthesis failed\n",
        encoding="utf-8",
    )
    (tmp_path / "research_progress.log").write_text(
        "2026-01-01T00:00:01+00:00 [done] retry succeeded\n",
        encoding="utf-8",
    )

    snapshot = merged_research_progress_full(str(tmp_path))

    assert snapshot.source_count == 2
    assert snapshot.lines[0].endswith(
        "[attempt:global_synthesis_1_failed_abc123] [error] first synthesis failed"
    )
    assert snapshot.lines[-1].endswith("[done] retry succeeded")


def test_full_progress_snapshot_fails_explicitly_instead_of_silently_truncating(tmp_path):
    (tmp_path / "research_progress.log").write_text("0123456789\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds the full-history safety limit"):
        merged_research_progress_full(str(tmp_path), max_total_bytes=4)


def test_progress_endpoint_serves_track_logs_before_root_merge(tmp_path, monkeypatch):
    track = tmp_path / "track_3"
    track.mkdir()
    (track / "research_progress.log").write_text(
        "2026-01-01T00:00:00+00:00 [init] client ready\n"
        "2026-01-01T00:00:01+00:00 [tool] web_search(query=x)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(PipelineManager, "handoff_dir", lambda _pipeline_id: str(tmp_path))
    monkeypatch.setattr(PipelineManager, "load", lambda _pipeline_id: {"status": "running"})

    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    response = app.test_client().get("/api/research/pipe_live/progress?lines=20")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source_count"] == 1
    assert payload["returned"] == 2
    assert payload["total"] is None
    assert payload["total_exact"] is False
    assert payload["lines"][-1].endswith("[track:3] [tool] web_search(query=x)")
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"


def test_progress_endpoint_serves_exact_full_history_on_explicit_scope(tmp_path, monkeypatch):
    track = tmp_path / "track_2"
    track.mkdir()
    (tmp_path / "research_progress.log").write_text(
        "2026-01-01T00:00:02+00:00 [done] root\n",
        encoding="utf-8",
    )
    (track / "research_progress.log").write_text(
        "2026-01-01T00:00:00+00:00 [init] first\n"
        "2026-01-01T00:00:01+00:00 [tool] second\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(PipelineManager, "handoff_dir", lambda _pipeline_id: str(tmp_path))
    monkeypatch.setattr(PipelineManager, "load", lambda _pipeline_id: {"status": "completed"})

    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    response = app.test_client().get("/api/research/pipe_live/progress?scope=full")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["scope"] == "full"
    assert payload["returned"] == payload["total"] == 3
    assert payload["total_exact"] is True
    assert payload["snapshot_exact"] is True
    assert payload["event_fidelity"] == "summarized_progress_events"
    assert payload["truncated"] is False
    assert payload["lines"][0].endswith("[track:2] [init] first")
    assert payload["lines"][-1].endswith("[done] root")


def test_progress_endpoint_returns_not_found_for_unknown_pipeline():
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")

    response = app.test_client().get(
        "/api/research/pipe_definitely_missing_loop013/progress?scope=full"
    )

    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_status_endpoint_is_never_cached(monkeypatch):
    monkeypatch.setattr(PipelineManager, "load", lambda _pipeline_id: {
        "pipeline_id": "pipe_live", "status": "running", "global_progress": 17,
    })
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")

    response = app.test_client().get("/api/research/status/pipe_live")

    assert response.status_code == 200
    assert response.get_json()["data"]["global_progress"] == 17
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"


def test_dossier_endpoint_surfaces_market_signals_status_and_charts(tmp_path, monkeypatch):
    (tmp_path / "research_report.md").write_text("# Report", encoding="utf-8")
    market_payload = {
        "as_of": "2026-07-11T00:00:00Z",
        "markets": [{"market_id": "691340", "implied_yes_prob": 0.1545}],
        "status": {"attempted": True, "selected_count": 1, "empty_reason": None},
    }
    (tmp_path / "prediction_markets.json").write_text(
        json.dumps(market_payload), encoding="utf-8")
    (tmp_path / "charts.json").write_text(
        json.dumps([{"id": "market_probabilities", "path": "charts/market.png"}]),
        encoding="utf-8")
    monkeypatch.setattr(PipelineManager, "handoff_dir", lambda _pipeline_id: str(tmp_path))

    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    response = app.test_client().get("/api/research/pipe_live/dossier")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["prediction_markets"] == market_payload
    assert data["charts"][0]["id"] == "market_probabilities"
