"""Regression tests for the postprocessed research contract boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import pipeline_orchestrator as po


def _write_generation(
    folder: Path,
    *,
    report: str,
    actors: dict | None = None,
    sources: list[dict] | None = None,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "research_report.md").write_text(report, encoding="utf-8")
    (folder / "meta.json").write_text(
        json.dumps({"producer": "global-synthesis"}), encoding="utf-8"
    )
    if actors is not None:
        (folder / "actors.json").write_text(
            json.dumps(actors), encoding="utf-8"
        )
    if sources is not None:
        (folder / "sources.json").write_text(
            json.dumps(sources), encoding="utf-8"
        )


def test_finalization_seals_lint_cast_and_effective_sources_together(tmp_path):
    handoff = tmp_path / "handoff"
    producer = tmp_path / "producer"
    original_report = "# Producer dossier\n\n" + "original evidence " * 60
    original_actors = {
        "actors": [{"name": "Actor A"}, {"name": "Actor A Inc."}],
        "relationships": [],
    }
    _write_generation(
        producer,
        report=original_report,
        actors=original_actors,
        # The producer omitted the effective merged ledger.  Finalization must
        # persist the exact in-memory union before sealing the new generation.
        sources=None,
    )
    first = po._promote_research_contract(str(producer), str(handoff))

    final_report = "# Final dossier\n\n" + "linted outcome evidence " * 60
    final_actors = {
        "actors": [{"name": "Actor A", "aliases": ["Actor A Inc."]}],
        "relationships": [],
    }
    final_sources = [{
        "url": "https://example.gov/evidence",
        "title": "Primary evidence",
        "tier": "S1",
    }]
    final_meta = {
        "producer": "global-synthesis",
        "research_budget": {"denials": 2},
    }

    # Sanctioned edits remain in memory while the producer root stays valid.
    assert po._validate_research_contract(str(handoff)) is True
    assert (handoff / "research_report.md").read_text() == original_report
    assert not (handoff / "sources.json").exists()

    finalized = po._finalize_research_contract(str(handoff), {
        "report": final_report,
        "actors": final_actors,
        "sources": final_sources,
        "meta": final_meta,
    })

    assert finalized is not None
    assert finalized["generation"] != first["generation"]
    assert po._validate_research_contract(str(handoff)) is True
    assert (handoff / "research_report.md").read_text() == final_report
    assert json.loads((handoff / "actors.json").read_text()) == final_actors
    assert json.loads((handoff / "sources.json").read_text()) == final_sources
    assert json.loads((handoff / "meta.json").read_text()) == final_meta
    assert finalized["files"]["research_report.md"]["sha256"] == (
        po._sha256_file(str(handoff / "research_report.md"))
    )
    assert finalized["files"]["actors.json"]["sha256"] == po._sha256_file(
        str(handoff / "actors.json")
    )
    assert finalized["files"]["sources.json"]["sha256"] == po._sha256_file(
        str(handoff / "sources.json")
    )
    assert not list(handoff.glob(".research-final-*"))
    assert not list(handoff.glob(".research-stage-*"))
    assert not list(handoff.glob(".research-rollback-*"))


def test_unchanged_resume_keeps_existing_generation(tmp_path, monkeypatch):
    handoff = tmp_path / "handoff"
    producer = tmp_path / "producer"
    report = "# Stable dossier\n\n" + "stable evidence " * 60
    actors = {"actors": [{"name": "Stable"}], "relationships": []}
    sources = [{"url": "https://example.gov/stable"}]
    _write_generation(
        producer, report=report, actors=actors, sources=sources
    )
    original = po._promote_research_contract(str(producer), str(handoff))
    monkeypatch.setattr(
        po.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: pytest.fail(
            "unchanged resume should not stage or recopy a generation"
        ),
    )

    finalized = po._finalize_research_contract(str(handoff), {
        "report": report,
        "actors": actors,
        "sources": sources,
        "meta": {"producer": "global-synthesis"},
    })

    assert finalized == original
    assert po._validate_research_contract(str(handoff)) is True


def test_budget_quality_patch_waits_for_atomic_contract_finalization(
    tmp_path, monkeypatch
):
    handoff = tmp_path / "handoff"
    producer = tmp_path / "producer"
    report = "# Budget dossier\n\n" + "budget evidence " * 60
    actors = {"actors": [{"name": "Budget Actor"}], "relationships": []}
    sources = [{"url": "https://example.gov/budget"}]
    _write_generation(
        producer, report=report, actors=actors, sources=sources
    )
    (producer / "meta.json").write_text(json.dumps({
        "producer": "global-synthesis",
        "research_quality": {"score": 0.9, "degraded": False},
    }), encoding="utf-8")
    po._promote_research_contract(str(producer), str(handoff))
    (handoff / "research_budget.json").write_text(json.dumps({
        "degraded": False,
        "global": {"attempts": 2, "denied_search_global": 1},
    }), encoding="utf-8")
    original_meta = (handoff / "meta.json").read_text()
    state = po.PipelineState(
        pipeline_id="pipe-budget-finalize", prompt="x", handoff_dir=str(handoff)
    )
    monkeypatch.setattr(
        po.PipelineManager, "save", classmethod(lambda cls, value: None)
    )

    final_meta = po.PipelineOrchestrator._surface_research_quality(
        None, state, str(handoff)
    )

    assert isinstance(final_meta, dict)
    assert final_meta["research_quality"]["degraded"] is True
    # Surface telemetry must not invalidate the currently published contract.
    assert (handoff / "meta.json").read_text() == original_meta
    assert po._validate_research_contract(str(handoff)) is True

    po._finalize_research_contract(str(handoff), {
        "report": report,
        "actors": actors,
        "sources": sources,
        "meta": final_meta,
    })

    assert json.loads((handoff / "meta.json").read_text()) == final_meta
    assert po._validate_research_contract(str(handoff)) is True


def test_finalization_install_failure_restores_prior_valid_generation(
    tmp_path, monkeypatch
):
    handoff = tmp_path / "handoff"
    producer = tmp_path / "producer"
    original_report = "# Producer dossier\n\n" + "stable evidence " * 60
    original_actors = {"actors": [{"name": "Original"}], "relationships": []}
    original_sources = [{"url": "https://example.gov/original"}]
    _write_generation(
        producer,
        report=original_report,
        actors=original_actors,
        sources=original_sources,
    )
    po._promote_research_contract(str(producer), str(handoff))
    original_manifest = (handoff / po._RESEARCH_CONTRACT_FILENAME).read_text()
    real_replace = po.os.replace

    def fail_during_final_install(src, dst):
        if (
            ".research-stage-" in str(src)
            and str(src).endswith("actors.json")
            and Path(dst) == handoff / "actors.json"
        ):
            raise OSError("simulated final contract install failure")
        return real_replace(src, dst)

    monkeypatch.setattr(po.os, "replace", fail_during_final_install)

    with pytest.raises(OSError, match="final contract install failure"):
        po._finalize_research_contract(str(handoff), {
            "report": "# Final dossier\n\n" + "new evidence " * 60,
            "actors": {"actors": [{"name": "Final"}], "relationships": []},
            "sources": [{"url": "https://example.gov/final"}],
        })

    assert (handoff / "research_report.md").read_text() == original_report
    assert json.loads((handoff / "actors.json").read_text()) == original_actors
    assert json.loads((handoff / "sources.json").read_text()) == original_sources
    assert (handoff / po._RESEARCH_CONTRACT_FILENAME).read_text() == original_manifest
    assert po._validate_research_contract(str(handoff)) is True
    assert not list(handoff.glob(".research-final-*"))
    assert not list(handoff.glob(".research-stage-*"))
    assert not list(handoff.glob(".research-rollback-*"))


def test_finalization_is_noop_for_legacy_unmanifested_handoff(tmp_path):
    handoff = tmp_path / "legacy"
    handoff.mkdir()
    original = "# Legacy dossier\n\n" + "legacy evidence " * 60
    (handoff / "research_report.md").write_text(original, encoding="utf-8")

    result = po._finalize_research_contract(str(handoff), {
        "report": "# Changed\n\n" + "changed evidence " * 60,
        "actors": None,
        "sources": None,
    })

    assert result is None
    assert (handoff / "research_report.md").read_text() == original
    assert not (handoff / po._RESEARCH_CONTRACT_FILENAME).exists()
