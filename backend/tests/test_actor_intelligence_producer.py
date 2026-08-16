"""Producer-side actor-intelligence/v1 regressions (offline and deterministic)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
BRIDGE_PY = REPO / "deerflow_bridge" / "deerflow_research.py"


@pytest.fixture(scope="module")
def dr():
    spec = importlib.util.spec_from_file_location(
        "deerflow_actor_intelligence_test", BRIDGE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _claim_text(name: str, dimension: str) -> str:
    return f"{name} has grounded {dimension} evidence."


def _source(*, names=("Acme",), purpose="actor-ontology"):
    excerpt_lines = [
        "Acme plans a conditional capacity expansion subject to permit approval.",
        "Acme and Beta signed a documented partnership agreement.",
    ]
    for name in names:
        excerpt_lines.extend(
            _claim_text(name, dimension)
            for dimension in (
                "identity_history",
                "incentives",
                "capabilities",
                "current_actions",
                "decision_rights_process_triggers",
            )
        )
    excerpt = "\n".join(excerpt_lines)
    content_sha256 = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    return {
        "url": "https://Example.com/actor-plan#section",
        "title": "Actor plan",
        "tier": "S1",
        "publication_date": "2026-06-30",
        "source_origin": "fetched",
        "reachable": True,
        "content_sha256": content_sha256,
        "excerpt": excerpt,
        "receipt_id": "receipt_actor_plan_1",
        "provider": "deerflow-web-fetch",
        "cache_hits": 2,
        "thread_id": "research-actor-thread",
        "lane": "outer-track-1",
        "purpose": purpose,
        "receipt_scopes": [{
            "thread_id": "research-actor-thread",
            "lane": "track-b",
            "purpose": purpose,
            "receipt_id": f"receipt_actor_plan_{index}",
            "content_sha256": content_sha256,
        } for index in (1, 2)],
    }


def _support(quote: str) -> dict:
    return {
        "source_ref": "https://example.com/actor-plan",
        "supporting_quote": quote,
    }


def _source_with_quote(quote: str, **kwargs) -> dict:
    source = _source(**kwargs)
    source["excerpt"] += "\n" + quote
    source["content_sha256"] = hashlib.sha256(
        source["excerpt"].encode("utf-8")
    ).hexdigest()
    for scope in source["receipt_scopes"]:
        scope["content_sha256"] = source["content_sha256"]
    return source


def _dimension_claim(name: str, dimension: str) -> dict:
    text = _claim_text(name, dimension)
    return {
        "claim": text,
        "evidence_type": "verified_fact",
        "claim_valid_at": "2026-07-01",
        "horizon": "current",
        "status": "observed",
        "confidence": "high",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(text)],
    }


def _gap(dimension: str, *, attempts: int = 2) -> dict:
    return {
        "reason": f"No grounded {dimension} evidence was found.",
        "attempted_queries": [
            f"{dimension} evidence query {index}"
            for index in range(1, attempts + 1)
        ],
        "receipt_ids": [
            f"receipt_actor_plan_{index}" for index in range(1, attempts + 1)
        ],
        "result_ids": [],
        "attempt_count": attempts,
        "exhausted": True,
    }


def _search_result_receipt(
    dr,
    index: int,
    *,
    query: str | None = None,
    result: str | None = None,
) -> dict:
    query = query or f"bounded actor evidence query {index}"
    result = result or f"bounded actor evidence result {index}"
    receipt = {
        "schema_version": dr._SEARCH_RESULT_RECEIPT_SCHEMA,
        "thread_id": "research-actor-thread",
        "lane": "track-b",
        "purpose": "actor-intelligence-coverage",
        "query": query,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
        "result_chars": len(result),
    }
    receipt["result_id"] = dr._search_result_receipt_id(receipt)
    return receipt


def _gap_search_result_receipt(dr, dimension: str, attempt: int) -> dict:
    return _search_result_receipt(
        dr,
        attempt,
        query=f"{dimension} evidence query {attempt}",
        result=f"{dimension} evidence search result {attempt}",
    )


def _dossier_search_result_receipts(dr) -> list[dict]:
    return [
        _gap_search_result_receipt(dr, dimension, attempt)
        for dimension in sorted(dr._critical_actor_gap_dimensions())
        for attempt in (1, 2)
    ]


def _actor(name="Acme", actor_type="company", actor_id="actor_modelchosen"):
    return {
        "actor_id": actor_id,
        "name": name,
        "type": actor_type,
        "simulation_tier": 1,
        "description": "A decision-making organization.",
        "intelligence": {
            "schema_version": "actor-intelligence/v1",
            "dimensions": {
                "future_plans": [{
                    "claim": "Plans a conditional capacity expansion.",
                    "evidence_type": "actor_stated_claim",
                    "claim_valid_at": "2026-07-01",
                    "horizon": "2027",
                    "status": "proposed",
                    "confidence": "medium",
                    "source_refs": ["https://example.com/actor-plan"],
                    "source_support": [_support(
                        "Acme plans a conditional capacity expansion subject to permit approval."
                    )],
                    "dependencies": ["permit approval"],
                    "contradictions": ["capital budget not approved"],
                    "amount": 2,
                    "unit": "GW",
                    "strategic_purpose": "increase capacity",
                }],
            },
            "evidence_gaps": {},
        },
    }


def _behavior_ready_actor(name="Acme"):
    actor = _actor(name=name)
    dimensions = actor["intelligence"]["dimensions"]
    dimensions.clear()
    for dimension in (
        "identity_history",
        "incentives",
        "capabilities",
        "current_actions",
        "decision_rights_process_triggers",
    ):
        dimensions[dimension] = [_dimension_claim(name, dimension)]
    return actor


def _actor_dossier(dr, names=("Acme",), *, all_gap=False):
    actors = []
    profiles = []
    behavior_dimensions = {
        "identity_history",
        "incentives",
        "capabilities",
        "current_actions",
        "decision_rights_process_triggers",
    }
    for name in names:
        dimensions = {}
        for dimension in dr.ACTOR_INTELLIGENCE_DIMENSIONS:
            covered = not all_gap and dimension in behavior_dimensions
            dimensions[dimension] = {
                "status": "covered" if covered else "gap",
                "source_refs": (
                    ["https://example.com/actor-plan"] if covered else []
                ),
                "claims": (
                    [_dimension_claim(name, dimension)] if covered else []
                ),
                "gap": None,
            }
            if not covered:
                critical = dimension in dr._critical_actor_gap_dimensions()
                dimensions[dimension]["gap"] = _gap(
                    dimension,
                    attempts=2 if critical else 1,
                )
                if critical:
                    dimensions[dimension]["gap"]["result_ids"] = [
                        _gap_search_result_receipt(
                            dr, dimension, attempt
                        )["result_id"]
                        for attempt in (1, 2)
                    ]
        actors.append({
            "name": name,
            "simulation_tier": 1,
            "dimensions": dimensions,
        })
        profiles.append(
            f"### Actor: {name}\n\n"
            + (
                "This substantive actor profile records sourced history, incentives, "
                "capabilities and constraints, current and planned action, capital "
                "allocation, decision process, likely responses, and explicit unknowns. "
            ) * 3
        )
    ledger = {
        "schema_version": dr.ACTOR_INTELLIGENCE_SCHEMA_VERSION,
        "actors": actors,
    }
    return (
        "# Actor dossier\n\n"
        + "\n\n".join(profiles)
        + "\n\n<!-- ACTOR_INTELLIGENCE_LEDGER_V1\n"
        + json.dumps(ledger)
        + "\n-->\n"
    )


def _write_dossier_coverage_sidecar(
    dr,
    out_dir: Path,
    dossier: str,
    sources: list[dict],
) -> dict:
    coverage = dr.actor_dossier_coverage_audit(
        dossier,
        sources,
        require_source_binding=True,
        required_receipt_purpose="track-b",
        required_receipt_thread_id="research-actor-thread",
        search_result_receipts=_dossier_search_result_receipts(dr),
    )
    assert coverage["accountable"] is True
    (out_dir / "actor_dossier_coverage.json").write_text(
        json.dumps(coverage), encoding="utf-8"
    )
    return coverage


def test_fresh_model_ids_are_ignored_and_type_drift_does_not_churn(dr):
    report = "final report"
    dossier = "final dossier"
    first = {"as_of_date": "2026-07-22", "actors": [_actor(actor_type="company")]}
    second = {"as_of_date": "2026-07-22", "actors": [_actor(actor_type="institution")]}
    dr.normalize_actor_intelligence_contract(
        first, report=report, dossier=dossier, sources=[_source()])
    dr.normalize_actor_intelligence_contract(
        second, report=report, dossier=dossier, sources=[_source()])

    expected = dr.stable_actor_id("Acme")
    assert expected != "actor_modelchosen"
    assert first["actors"][0]["actor_id"] == expected
    assert second["actors"][0]["actor_id"] == expected


def test_same_name_homonyms_are_rejected_instead_of_order_disambiguated(dr):
    obj = {
        "actors": [
            _actor(name="Phoenix", actor_type="company", actor_id=""),
            _actor(name="Phoenix", actor_type="agency", actor_id=""),
        ],
    }
    with pytest.raises(ValueError, match="homonym multiplicity"):
        dr.normalize_actor_intelligence_contract(
            obj, report="r", dossier="d", sources=[_source(names=("Phoenix",))])


def test_nfkc_equivalent_names_and_aliases_share_one_identity_namespace(dr):
    assert dr._cast_norm("Ａｃｍｅ") == dr._cast_norm("Acme")

    with pytest.raises(ValueError, match="homonym multiplicity"):
        dr.normalize_actor_intelligence_contract(
            {"actors": [_actor(name="Acme"), _actor(name="Ａｃｍｅ")]},
            report="r",
            dossier="d",
            sources=[_source()],
        )

    aliased = _actor(name="Alpha")
    aliased["aliases"] = ["Ａｃｍｅ"]
    with pytest.raises(ValueError, match="alias namespace overlap"):
        dr.normalize_actor_intelligence_contract(
            {"actors": [aliased, _actor(name="Acme")]},
            report="r",
            dossier="d",
            sources=[_source(names=("Alpha", "Acme"))],
        )


def test_ambiguous_duplicate_or_empty_actor_identity_fails_closed(dr):
    duplicate = {
        "actors": [
            _actor(name="Phoenix", actor_type="company", actor_id=""),
            _actor(name="Phoenix", actor_type="company", actor_id=""),
        ],
    }
    with pytest.raises(ValueError, match="cannot deterministically disambiguate"):
        dr.normalize_actor_intelligence_contract(
            duplicate, report="r", dossier="d", sources=[_source()])

    empty = {"actors": [_actor(name="", actor_id="")]}
    with pytest.raises(ValueError, match="without a canonical name"):
        dr.normalize_actor_intelligence_contract(
            empty, report="r", dossier="d", sources=[_source()])


def test_normalizer_preserves_epistemics_qualifiers_and_explicit_gaps(dr):
    sources = [_source()]
    actor = _actor()
    structured_gap = _gap("motivations")
    actor["intelligence"]["evidence_gaps"]["motivations"] = [
        structured_gap
    ]
    obj = {"as_of_date": "2026-07-22", "actors": [actor]}
    contract = dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier", sources=sources)
    intelligence = obj["actors"][0]["intelligence"]
    claim = intelligence["dimensions"]["future_plans"][0]

    assert set(intelligence["dimensions"]) == set(
        dr.ACTOR_INTELLIGENCE_DIMENSIONS)
    assert claim["evidence_type"] == "actor_stated_claim"
    assert claim["status"] == "proposed"
    assert claim["source_refs"] == [sources[0]["source_id"]]
    assert claim["qualifiers"] == {
        "amount": 2,
        "unit": "GW",
        "strategic_purpose": "increase capacity",
    }
    assert intelligence["dimensions"]["motivations"] == []
    assert intelligence["evidence_gaps"]["motivations"] == [structured_gap]
    assert isinstance(
        intelligence["evidence_gaps"]["motivations"][0], dict
    )
    assert isinstance(
        intelligence["evidence_gaps"]["constraints"][0], dict
    )
    assert contract["dimensions"] == list(dr.ACTOR_INTELLIGENCE_DIMENSIONS)


def test_normalizer_preserves_nested_forward_and_knowledge_qualifiers(dr):
    actor = _actor()
    quote = "Project Aurora expansion is conditional on a board vote."
    actor["intelligence"]["dimensions"]["future_plans"][0] = {
        "claim": quote,
        "project": "",
        "actor_knows": "",
        "evidence_type": "actor_stated_claim",
        "as_of_date": "2026-07-01",
        "horizon": "2027",
        "status": "proposed",
        "confidence": "medium",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
        "qualifiers": {
            "project": "Project Aurora",
            "counterparty": "Regional Grid Board",
            "geography": "North region",
            "decision_kind": "board_approval",
            "trigger": "permit approval",
            "actor_knows": True,
            "visibility": "actor_internal",
        },
    }
    obj = {"as_of_date": "2026-07-22", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier",
        sources=[_source_with_quote(quote)])

    qualifiers = obj["actors"][0]["intelligence"]["dimensions"][
        "future_plans"
    ][0]["qualifiers"]
    assert qualifiers == {
        "project": "Project Aurora",
        "counterparty": "Regional Grid Board",
        "geography": "North region",
        "decision_kind": "board_approval",
        "trigger": "permit approval",
        "actor_knows": True,
        "visibility": "actor_internal",
    }


def test_normalizer_preserves_bounded_incentive_payoff_qualifiers(dr):
    actor = _actor()
    quote = "The actor is rewarded for commissioning capacity on schedule."
    actor["intelligence"]["dimensions"]["incentives"] = [{
        "claim": quote,
        "evidence_type": "verified_fact",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
        "qualifiers": {
            "driver": "delivery-linked compensation",
            "gains_if": "capacity enters service before the deadline",
            "loses_if": "the permit or capital budget slips",
            "intensity": "high",
            "unbounded_private_psychology": "must be dropped",
        },
    }]
    obj = {"as_of_date": "2026-07-22", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier",
        sources=[_source_with_quote(quote)])

    qualifiers = obj["actors"][0]["intelligence"]["dimensions"][
        "incentives"
    ][0]["qualifiers"]
    assert qualifiers == {
        "driver": "delivery-linked compensation",
        "gains_if": "capacity enters service before the deadline",
        "loses_if": "the permit or capital budget slips",
        "intensity": "high",
    }


def test_normalizer_drops_ambiguous_knowledge_visibility_and_non_boolean_flag(dr):
    actor = _actor()
    quote = "The actor may have seen a private memo."
    actor["intelligence"]["dimensions"]["knowledge_state"] = [{
        "claim": quote,
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
        "qualifiers": {
            "actor_knows": "true",
            "visibility": "make this secret omniscient",
        },
    }]
    obj = {"as_of_date": "2026-07-22", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier",
        sources=[_source_with_quote(quote)])

    qualifiers = obj["actors"][0]["intelligence"]["dimensions"][
        "knowledge_state"
    ][0]["qualifiers"]
    assert "actor_knows" not in qualifiers
    assert "visibility" not in qualifiers


def test_normal_finalizer_hashes_exact_final_report_and_rewrites_sources(dr, tmp_path):
    report = "# Final judged report\n\n" + "final prose " * 60
    dossier = _actor_dossier(dr)
    (tmp_path / dr.ACTORS_FILENAME).write_text(
        json.dumps({
            "as_of_date": "2026-07-22",
            "actors": [_behavior_ready_actor()],
        }),
        encoding="utf-8",
    )
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([_source()]), encoding="utf-8")
    _write_dossier_coverage_sidecar(
        dr, tmp_path, dossier, [_source()]
    )
    log = dr.ProgressLog(tmp_path / "progress.log")
    meta = {}
    contract = dr.persist_final_actor_intelligence_contract(
        tmp_path, report=report, dossier=dossier, meta=meta, plog=log)
    log.close()

    persisted = json.loads(
        (tmp_path / dr.ACTORS_FILENAME).read_text(encoding="utf-8"))
    persisted_sources = json.loads(
        (tmp_path / dr.SOURCES_FILENAME).read_text(encoding="utf-8"))
    assert contract is not None
    assert contract["report_sha256"] == hashlib.sha256(
        report.encode("utf-8")).hexdigest()
    assert persisted["actor_intelligence_contract"] == contract
    assert persisted_sources[0]["source_id"].startswith("src_")
    assert meta["actor_intelligence"]["report_sha256"] == contract["report_sha256"]


def test_extract_only_seals_report_after_chart_mutation(dr, tmp_path, monkeypatch):
    initial_report = "# Existing report\n\n" + "evidence " * 100
    report_path = tmp_path / dr.REPORT_FILENAME
    report_path.write_text(initial_report, encoding="utf-8")

    (tmp_path / dr.ACTOR_DOSSIER_FILENAME).write_text(
        _actor_dossier(dr), encoding="utf-8")
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([_source()]), encoding="utf-8")
    _write_dossier_coverage_sidecar(
        dr, tmp_path, _actor_dossier(dr), [_source()]
    )
    obj = {
        "as_of_date": "2026-07-22",
        "actors": [_behavior_ready_actor()],
        "sources": [_source()],
    }
    (tmp_path / dr.ACTORS_FILENAME).write_text(
        json.dumps({"actors": [_behavior_ready_actor()]}),
        encoding="utf-8",
    )
    dr._write_actor_artifact_lineage(
        tmp_path,
        report=initial_report,
        dossier=_actor_dossier(dr),
        meta={
            "question": "Question",
            "depth": "standard",
            "thread_id": "research-actor-thread",
        },
        contract={},
    )
    monkeypatch.setattr(
        dr,
        "extract_complete_structured_tool_free",
        lambda *_args, **_kwargs: ("{}", obj, [], False),
    )
    monkeypatch.setattr(dr, "_collect_prediction_markets", lambda *_a, **_k: None)

    final_report = initial_report + "\n\n## Visual Annex\n\nFinal chart reference.\n"

    def mutate_report(out_dir, *_args, **_kwargs):
        (Path(out_dir) / dr.REPORT_FILENAME).write_text(
            final_report, encoding="utf-8")
        return {"passed": True}

    monkeypatch.setattr(dr, "_render_research_charts", mutate_report)
    args = SimpleNamespace(
        no_actors=False,
        target_language="English",
        model="test-model",
        depth="standard",
    )
    meta = {}
    log = dr.ProgressLog(tmp_path / "extract.log")
    assert dr.run_extract_only(
        "Question", tmp_path, args, meta, log, lambda: None) == 0
    persisted = json.loads(
        (tmp_path / dr.ACTORS_FILENAME).read_text(encoding="utf-8"))
    assert persisted["actor_intelligence_contract"]["report_sha256"] == (
        hashlib.sha256(final_report.encode("utf-8")).hexdigest()
    )


def test_extract_only_lineage_rejects_stale_other_question_inputs(dr, tmp_path):
    report = "# Existing report\n\n" + "evidence " * 100
    dossier = _actor_dossier(dr)
    (tmp_path / dr.REPORT_FILENAME).write_text(report, encoding="utf-8")
    (tmp_path / dr.ACTOR_DOSSIER_FILENAME).write_text(
        dossier, encoding="utf-8")
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([_source()]), encoding="utf-8")
    (tmp_path / dr.ACTORS_FILENAME).write_text(
        json.dumps({"actors": [_behavior_ready_actor()]}), encoding="utf-8")
    dr._write_actor_artifact_lineage(
        tmp_path,
        report=report,
        dossier=dossier,
        meta={"question": "Original question", "depth": "standard"},
        contract={},
    )

    with pytest.raises(
        dr.ActorIntelligenceFinalizationError,
        match="question mismatch",
    ):
        dr.validate_actor_artifact_lineage(
            tmp_path,
            question="Different question",
            depth="standard",
        )


def test_required_finalizer_rejects_missing_empty_or_stale_actor_artifacts(
        dr, tmp_path):
    log = dr.ProgressLog(tmp_path / "finalizer.log")
    with pytest.raises(dr.ActorIntelligenceFinalizationError, match="missing"):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=_actor_dossier(dr),
            meta={},
            plog=log,
            required=True,
        )

    actor_path = tmp_path / dr.ACTORS_FILENAME
    actor_path.write_text(json.dumps({"actors": []}), encoding="utf-8")
    with pytest.raises(dr.ActorIntelligenceFinalizationError, match="nonempty"):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=_actor_dossier(dr),
            meta={},
            plog=log,
            required=True,
        )

    actor_path.write_text(json.dumps({
        "actors": [_behavior_ready_actor()],
    }), encoding="utf-8")
    with pytest.raises(dr.ActorIntelligenceFinalizationError, match="current extraction"):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=_actor_dossier(dr),
            meta={},
            plog=log,
            required=True,
            require_current_extraction=True,
            expected_unsealed_actors_sha256="",
        )
    log.close()


def test_required_finalizer_binds_dossier_and_extracted_tier_1_2_rosters(
        dr, tmp_path):
    actor_path = tmp_path / dr.ACTORS_FILENAME
    actor_path.write_text(json.dumps({
        "as_of_date": "2026-07-22",
        "actors": [_behavior_ready_actor("Acme")],
    }), encoding="utf-8")
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([_source(names=("Different Actor",))]), encoding="utf-8")
    _write_dossier_coverage_sidecar(
        dr,
        tmp_path,
        _actor_dossier(dr, names=("Different Actor",)),
        [_source(names=("Different Actor",))],
    )
    log = dr.ProgressLog(tmp_path / "roster.log")

    with pytest.raises(dr.ActorIntelligenceFinalizationError, match="roster mismatch"):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=_actor_dossier(dr, names=("Different Actor",)),
            meta={},
            plog=log,
            required=True,
        )
    log.close()


def test_ungrounded_claims_are_omitted_from_behavior_and_audited(dr):
    actor = _behavior_ready_actor()
    actor["intelligence"]["dimensions"].setdefault("future_plans", []).append({
        "claim": "Unsupported plan must never enter the runtime persona.",
        "evidence_type": "actor_stated_claim",
        "source_refs": ["https://not-fetched.invalid/claim"],
    })
    actor["intelligence"]["dimensions"]["red_lines"] = [{
        "claim": "A cited-but-unfetched red line must also be omitted.",
        "evidence_type": "analyst_inference",
        "source_refs": ["https://example.com/cited-only"],
    }]
    obj = {"as_of_date": "2026-07-22", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj,
        report="report",
        dossier="dossier",
        sources=[
            _source(),
            {
                "url": "https://example.com/cited-only",
                "title": "Search result not opened",
                "source_origin": "cited",
            },
        ],
    )

    intelligence = obj["actors"][0]["intelligence"]
    plans = [row["claim"] for row in intelligence["dimensions"]["future_plans"]]
    assert "Unsupported plan must never enter the runtime persona." not in plans
    assert intelligence["dimensions"]["red_lines"] == []
    assert all("claim" not in row for row in intelligence["omission_audit"])
    omitted = {
        (row["dimension"], row["reason"])
        for row in intelligence["omission_audit"]
    }
    assert (
        "future_plans", "no_quote_bound_fetched_source_support"
    ) in omitted
    assert (
        "red_lines", "no_quote_bound_fetched_source_support"
    ) in omitted


def test_fetched_receipt_provenance_survives_sources_and_contract(dr):
    dr._reset_fetched_sources()
    dr._FETCHED_SOURCES.append({
        "url": "https://example.com/actor-plan",
        "ok": True,
        "content_sha256": "b" * 64,
        "receipt_id": "receipt_exact_fetch",
        "provider": "browserless",
        "cache_hits": 3,
        "content_chars": 4210,
    })
    manifest_sources = dr.export_fetched_sources_for_manifest()
    assert manifest_sources[0]["content_sha256"] == "b" * 64
    assert manifest_sources[0]["receipt_id"] == "receipt_exact_fetch"
    assert manifest_sources[0]["provider"] == "browserless"
    assert manifest_sources[0]["cache_hits"] == 3
    dr._reset_fetched_sources()
    assert dr.seed_manifest_sources(manifest_sources) == 1
    sources, dropped = dr.merge_fetched_into_sources([_source()])
    assert dropped == 0
    assert sources[0]["content_sha256"] == "b" * 64
    assert sources[0]["receipt_id"] == "receipt_exact_fetch"
    assert sources[0]["provider"] == "browserless"
    assert sources[0]["cache_hits"] == 3
    assert sources[0]["content_chars"] == 4210

    obj = {"as_of_date": "2026-07-22", "actors": [_behavior_ready_actor()]}
    contract = dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier", sources=sources)
    provenance = contract["source_provenance"]
    assert provenance["fetched_source_count"] == 1
    assert provenance["content_hash_count"] == 1
    assert provenance["receipt_count"] == 1
    assert provenance["providers"] == ["browserless"]
    assert provenance["cache_hit_total"] == 3
    assert len(provenance["sha256"]) == 64
    dr._reset_fetched_sources()


def test_extract_only_actor_failure_is_nonzero_but_explicit_no_actors_is_allowed(
        dr, tmp_path, monkeypatch):
    report = "# Existing report\n\n" + "evidence " * 100
    (tmp_path / dr.REPORT_FILENAME).write_text(report, encoding="utf-8")
    monkeypatch.setattr(
        dr,
        "extract_complete_structured_tool_free",
        lambda *_args, **_kwargs: ("", None, [], False),
    )
    monkeypatch.setattr(dr, "_collect_prediction_markets", lambda *_a, **_k: None)
    monkeypatch.setattr(dr, "_render_research_charts", lambda *_a, **_k: {})

    required_log = dr.ProgressLog(tmp_path / "extract-required.log")
    required_args = SimpleNamespace(
        no_actors=False,
        target_language="English",
        model="test-model",
        depth="standard",
    )
    required_meta = {}
    assert dr.run_extract_only(
        "Question", tmp_path, required_args, required_meta, required_log,
        lambda: None,
    ) != 0
    assert required_meta["status"] == "failed"

    no_actor_log = dr.ProgressLog(tmp_path / "extract-no-actors.log")
    no_actor_args = SimpleNamespace(
        no_actors=True,
        target_language="English",
        model="test-model",
        depth="standard",
    )
    no_actor_meta = {}
    assert dr.run_extract_only(
        "Question", tmp_path, no_actor_args, no_actor_meta, no_actor_log,
        lambda: None,
    ) == 0
    assert no_actor_meta["status"] == "completed"


def test_extract_only_never_upgrades_model_citations_to_fetched_provenance(
        dr, tmp_path, monkeypatch):
    report = "# Existing report\n\n" + "evidence " * 100
    (tmp_path / dr.REPORT_FILENAME).write_text(report, encoding="utf-8")
    (tmp_path / dr.ACTOR_DOSSIER_FILENAME).write_text(
        _actor_dossier(dr), encoding="utf-8")
    model_obj = {
        "as_of_date": "2026-07-22",
        "actors": [_behavior_ready_actor()],
        # Even a model-emitted fetched marker is untrusted in extract-only: the
        # process performed no fetch and there is no prior sources.json receipt.
        "sources": [_source()],
    }
    monkeypatch.setattr(
        dr,
        "extract_complete_structured_tool_free",
        lambda *_args, **_kwargs: ("{}", model_obj, [], False),
    )
    monkeypatch.setattr(dr, "_collect_prediction_markets", lambda *_a, **_k: None)
    monkeypatch.setattr(dr, "_render_research_charts", lambda *_a, **_k: {})
    args = SimpleNamespace(
        no_actors=False,
        target_language="English",
        model="test-model",
        depth="standard",
    )
    meta = {}
    log = dr.ProgressLog(tmp_path / "extract-model-citations.log")

    assert dr.run_extract_only(
        "Question", tmp_path, args, meta, log, lambda: None) != 0
    assert not (tmp_path / dr.SOURCES_FILENAME).exists()
    assert "lineage" in meta["error"]
    assert meta["status"] == "failed"


def test_prompts_and_outline_require_cast_wide_forward_behavior(dr):
    extraction = dr.build_extraction_recovery_prompt("English")
    synthesis = dr.build_synthesis_prompt("Question", "English", "deep")
    outline = dr.enforce_synthesis_outline_contract([], "Question")
    joined = " ".join(
        f"{row['title']} {row['scope']}" for row in outline)

    assert "actor-intelligence/v1" in extraction
    assert "actor_stated_claim" in extraction
    assert "investments_capital_allocation" in extraction
    assert "simulation_tier (1=principal, 2=stakeholder" in extraction
    assert "actor_knows MUST be a literal JSON boolean" in extraction
    assert "driver, gains_if, loses_if, and intensity" in extraction
    assert "supporting_quote" in extraction
    assert "supporting_span" in extraction
    assert "receipt_id" in extraction
    assert "content_sha256" in extraction
    assert "source_publication_date" in extraction
    assert "claim_valid_at" in extraction
    assert "attempted_queries" in extraction
    assert "result_ids" in extraction
    full_extraction = dr.build_extraction_prompt("English", "deep")
    assert '"visibility": "public"|"actor_known"' in full_extraction
    assert '"actor_knows": true|false' in full_extraction
    assert '"driver"|"gains_if"|"loses_if"|"intensity"' in full_extraction
    assert '"source_support"' in full_extraction
    assert '"supporting_quote"' in full_extraction
    assert '"claim_valid_at"' in full_extraction
    assert '"attempted_queries"' in full_extraction
    assert "dedicated ACTOR INTELLIGENCE" in synthesis
    assert "future plans with status/horizon/dependencies" in joined


def test_coverage_audit_rejects_invented_refs_when_source_binding_required(dr):
    cells = {
        dimension: {
            "status": "covered",
            "source_refs": ["https://invented.invalid/not-fetched"],
            "gap": "",
        }
        for dimension in dr.ACTOR_INTELLIGENCE_DIMENSIONS
    }
    ledger = {
        "schema_version": dr.ACTOR_INTELLIGENCE_SCHEMA_VERSION,
        "actors": [{
            "name": "Acme",
            "simulation_tier": 1,
            "dimensions": cells,
        }],
    }
    dossier = (
        "### Actor: Acme\n\n"
        + "Substantive sourced profile of history, incentives, capabilities, "
        "actions, plans, investments, decision process, and likely behavior. " * 3
        + "\n\n"
        "<!-- ACTOR_INTELLIGENCE_LEDGER_V1\n"
        + json.dumps(ledger)
        + "\n-->"
    )
    assert dr.actor_dossier_coverage_audit(dossier)["accountable"] is True
    bound = dr.actor_dossier_coverage_audit(
        dossier, [_source()], require_source_binding=True)
    assert bound["accountable"] is False
    assert any(
        "covered_without_fetched_source" in error
        for error in bound["errors"]
    )

    assert any("covered_without_claims" in error for error in bound["errors"])


def test_dossier_audit_rejects_all_gap_plane_with_explicit_family_failures(dr):
    audit = dr.actor_dossier_coverage_audit(
        _actor_dossier(dr, all_gap=True),
        [_source()],
        require_source_binding=True,
        search_result_receipts=_dossier_search_result_receipts(dr),
    )

    assert audit["accountable"] is False
    assert audit["behavior_ready_family_count"] == 0
    assert set(audit["required_behavior_ready_families"]) == {
        "identity_history",
        "incentives_motivations_values",
        "capabilities_constraints",
        "actions_plans_investments",
        "decision_likely_actions_red_lines",
    }
    assert len(audit["behavior_ready_family_failures"]) == 5
    assert any("all_dimensions_gap" in error for error in audit["errors"])


def test_dossier_audit_accepts_one_grounded_dimension_per_required_family(dr):
    audit = dr.actor_dossier_coverage_audit(
        _actor_dossier(dr),
        [_source()],
        require_source_binding=True,
        search_result_receipts=_dossier_search_result_receipts(dr),
    )

    assert audit["accountable"] is True
    assert audit["behavior_ready_family_count"] == 5
    assert audit["behavior_ready_family_failures"] == []


@pytest.mark.parametrize(
    ("lane", "thread_id"),
    [("track-a", "research-actor-thread"), ("track-b", "stale-thread")],
)
def test_track_b_admission_requires_current_lane_thread_and_actor_purpose(
    dr, lane, thread_id
):
    source = _source()
    for scope in source["receipt_scopes"]:
        scope["lane"] = lane
        scope["thread_id"] = thread_id

    audit = dr.actor_dossier_coverage_audit(
        _actor_dossier(dr),
        [source],
        require_source_binding=True,
        required_receipt_purpose="track-b",
        required_receipt_thread_id="research-actor-thread",
        search_result_receipts=_dossier_search_result_receipts(dr),
    )

    assert audit["accountable"] is False
    assert any(
        "covered_without_track_b_receipt" in error
        for error in audit["errors"]
    )


def test_gap_result_ids_must_resolve_to_current_producer_receipt_ledger(dr):
    receipts = _dossier_search_result_receipts(dr)
    gap_receipts = [
        row for row in receipts
        if row["query"].startswith("values_worldview evidence query ")
    ]

    def use_result_ids(ledger):
        gap = ledger["actors"][0]["dimensions"]["values_worldview"]["gap"]
        gap["receipt_ids"] = []
        gap["result_ids"] = [row["result_id"] for row in gap_receipts]

    valid_dossier = _replace_ledger(_actor_dossier(dr), use_result_ids)
    valid = dr.actor_dossier_coverage_audit(
        valid_dossier,
        [_source()],
        require_source_binding=True,
        required_receipt_purpose="track-b",
        required_receipt_thread_id="research-actor-thread",
        search_result_receipts=receipts,
    )
    assert valid["accountable"] is True

    def invent_result_ids(ledger):
        gap = ledger["actors"][0]["dimensions"]["values_worldview"]["gap"]
        gap["receipt_ids"] = []
        gap["result_ids"] = ["invented-result-1", "invented-result-2"]

    invented = dr.actor_dossier_coverage_audit(
        _replace_ledger(_actor_dossier(dr), invent_result_ids),
        [_source()],
        require_source_binding=True,
        required_receipt_purpose="track-b",
        required_receipt_thread_id="research-actor-thread",
        search_result_receipts=receipts,
    )
    assert invented["accountable"] is False
    assert any("gap_result_id_unbound" in error for error in invented["errors"])


def test_critical_gap_requires_two_distinct_query_bound_search_results(dr):
    receipts = _dossier_search_result_receipts(dr)
    target_receipts = [
        row for row in receipts
        if row["query"].startswith("values_worldview evidence query ")
    ]

    def audit_with(result_ids, extra_receipts=()):
        def update_gap(ledger):
            gap = ledger["actors"][0]["dimensions"][
                "values_worldview"
            ]["gap"]
            gap["result_ids"] = list(result_ids)

        return dr.actor_dossier_coverage_audit(
            _replace_ledger(_actor_dossier(dr), update_gap),
            [_source()],
            require_source_binding=True,
            required_receipt_purpose="track-b",
            required_receipt_thread_id="research-actor-thread",
            search_result_receipts=[*receipts, *extra_receipts],
        )

    fetch_plus_one_result = audit_with([
        target_receipts[0]["result_id"]
    ])
    assert fetch_plus_one_result["accountable"] is False
    assert any(
        "critical_gap_query_result_attempts_lt_2" in error
        for error in fetch_plus_one_result["errors"]
    )

    same_query_second_result = _search_result_receipt(
        dr,
        2,
        query=target_receipts[0]["query"],
        result="a different result from the same query",
    )
    same_query = audit_with(
        [
            target_receipts[0]["result_id"],
            same_query_second_result["result_id"],
        ],
        [same_query_second_result],
    )
    assert same_query["accountable"] is False
    assert any(
        "critical_gap_query_result_attempts_lt_2" in error
        for error in same_query["errors"]
    )

    unrelated = _search_result_receipt(dr, 99)
    mismatched = audit_with(
        [target_receipts[0]["result_id"], unrelated["result_id"]],
        [unrelated],
    )
    assert mismatched["accountable"] is False
    assert any(
        "gap_result_query_mismatch" in error
        for error in mismatched["errors"]
    )


def test_search_result_receipt_is_produced_only_from_exact_call_result_pair(dr):
    dr._reset_fetched_sources()
    dr._set_actor_track_thread_id("research-actor-thread")
    pending = []
    scope = dr._turn_receipt_scope(
        "actor-intelligence-coverage", "research-actor-thread"
    )
    dr._pending_record_search(
        pending,
        "web_search",
        {"query": "Acme actor evidence"},
        call_id="call-1",
        receipt_scope=scope,
    )
    dr._pending_mark_search_result(
        pending,
        "web_search",
        "Search result body with evidence.",
        call_id="wrong-call",
    )
    assert dr._track_b_search_result_receipts() == []

    dr._pending_mark_search_result(
        pending,
        "web_search",
        "Search result body with evidence.",
        call_id="call-1",
    )
    receipts = dr._track_b_search_result_receipts()
    assert len(receipts) == 1
    assert receipts[0]["result_id"].startswith("search_result_")
    assert receipts[0]["query"] == "Acme actor evidence"
    assert receipts[0]["thread_id"] == "research-actor-thread"
    assert receipts[0]["lane"] == "track-b"
    tampered = dict(receipts[0])
    tampered["query"] = "Different actor evidence"
    assert dr._validated_search_result_receipt(
        tampered, required_thread_id="research-actor-thread"
    ) is None
    dr._reset_fetched_sources()


def test_missing_ai_judge_cannot_bless_an_all_gap_shared_actor_plane(
        dr, tmp_path, monkeypatch):
    all_gap = _actor_dossier(dr, all_gap=True)

    def fake_turn(*_args, **_kwargs):
        label = _args[-1] if _args else _kwargs.get("label", "")
        return "" if label == "actor-intelligence-coverage" else all_gap

    class MissingThreadClient:
        def get_thread(self, _thread_id):
            raise RuntimeError("offline fixture has no checkpoint thread")

    monkeypatch.setattr(dr, "run_streamed_turn", fake_turn)
    monkeypatch.setattr(dr, "judge_dossier", lambda *_a, **_k: None)
    monkeypatch.setenv("ACTOR_DOSSIER_JUDGE", "true")
    monkeypatch.setenv("ACTOR_DOSSIER_JUDGE_MAX_ROUNDS", "1")
    log = dr.ProgressLog(tmp_path / "missing-judge.log")

    result = dr.run_actor_ontology_stage(
        MissingThreadClient(),
        "Question",
        "standard",
        "English",
        "test-model",
        "thread",
        log,
        out_dir=tmp_path,
    )
    log.close()

    assert result == ""
    coverage = json.loads(
        (tmp_path / "actor_dossier_coverage.json").read_text(encoding="utf-8"))
    assert coverage["accountable"] is False
    assert len(coverage["behavior_ready_family_failures"]) == 5


def _replace_ledger(dossier: str, transform) -> str:
    marker = "<!-- ACTOR_INTELLIGENCE_LEDGER_V1\n"
    start = dossier.index(marker) + len(marker)
    end = dossier.index("\n-->", start)
    ledger = json.loads(dossier[start:end])
    transform(ledger)
    return dossier[:start] + json.dumps(ledger) + dossier[end:]


@pytest.mark.parametrize(
    "raw",
    ["tier 10", "1-ish", "principal 1 / stakeholder 2", "v1", "21"],
)
def test_simulation_tier_parser_never_extracts_an_embedded_one(dr, raw):
    assert dr._actor_explicit_tier({"simulation_tier": raw}) is None


@pytest.mark.parametrize("raw, expected", [(1, 1), ("1", 1), (4, 4), ("4", 4)])
def test_simulation_tier_parser_accepts_only_exact_enum_values(dr, raw, expected):
    assert dr._actor_explicit_tier({"simulation_tier": raw}) == expected


def test_v1_normalizer_infers_persists_and_demotes_non_simulation_tiers(dr):
    principal = _actor(name="Principal")
    principal.pop("simulation_tier")
    principal["influence"] = "high"
    stakeholder = _actor(name="Stakeholder")
    stakeholder.pop("simulation_tier")
    stakeholder["influence"] = "medium"
    context = _actor(name="Context")
    context["simulation_tier"] = 4
    context["archetype"] = "asset_object"
    source = _source(names=("Principal", "Stakeholder", "Context"))
    obj = {"actors": [principal, stakeholder, context]}

    dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[source])

    assert [(row["name"], row["simulation_tier"]) for row in obj["actors"]] == [
        ("Principal", 1),
        ("Stakeholder", 2),
    ]
    assert [(row["name"], row["simulation_tier"]) for row in obj["context_entities"]] == [
        ("Context", 4),
    ]


def test_reciprocal_alias_overlap_fails_v1_identity_seal(dr):
    first = _actor(name="Alpha")
    first["aliases"] = ["Beta"]
    second = _actor(name="Beta")
    second["aliases"] = ["Alpha"]

    with pytest.raises(ValueError, match="alias namespace overlap"):
        dr.normalize_actor_intelligence_contract(
            {"actors": [first, second]},
            report="r",
            dossier="d",
            sources=[_source(names=("Alpha", "Beta"))],
        )


def test_claim_support_binds_exact_quote_receipt_hash_and_publication_date(dr):
    source = _source()
    actor = _actor()
    obj = {"as_of_date": "2099-01-01", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="report", dossier="dossier", sources=[source])

    claim = obj["actors"][0]["intelligence"]["dimensions"]["future_plans"][0]
    assert claim["claim_valid_at"] == "2026-07-01"
    assert claim["as_of_date"] == "2026-07-01"
    assert claim["source_refs"] == [source["source_id"]]
    assert claim["source_support"] == [{
        "source_id": source["source_id"],
        "supporting_quote": (
            "Acme plans a conditional capacity expansion subject to permit approval."
        ),
        "supporting_span": {
            "basis": "exact_excerpt",
            "start": 0,
            "end": 71,
        },
        "receipt_id": "receipt_actor_plan_1",
        "content_sha256": source["content_sha256"],
        "source_publication_date": "2026-06-30",
        "thread_id": "research-actor-thread",
        "lane": "track-b",
        "purpose": "actor-ontology",
    }]
    assert claim["claim_id"].startswith("claim_")
    assert len(claim["claim_sha256"]) == 64


def test_unrelated_fetched_url_and_missing_quote_cannot_ground_a_claim(dr):
    actor = _behavior_ready_actor()
    actor["intelligence"]["dimensions"]["future_plans"] = [{
        "claim": "Acme will acquire a nuclear utility.",
        "evidence_type": "actor_stated_claim",
        "claim_valid_at": "2026-07-01",
        "status": "proposed",
        "horizon": "2028",
        "confidence": "high",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(
            "Acme will acquire a nuclear utility."
        )],
    }]
    obj = {"actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[_source()])

    assert obj["actors"][0]["intelligence"]["dimensions"]["future_plans"] == []
    assert any(
        row["reason"] == "no_quote_bound_fetched_source_support"
        for row in obj["actors"][0]["intelligence"]["omission_audit"]
    )


def test_global_as_of_never_launders_a_missing_claim_valid_at(dr):
    actor = _behavior_ready_actor()
    claim = actor["intelligence"]["dimensions"]["current_actions"][0]
    claim.pop("claim_valid_at")
    obj = {"as_of_date": "2099-01-01", "actors": [actor]}

    dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[_source()])

    normalized = obj["actors"][0]["intelligence"]["dimensions"]["current_actions"][0]
    assert normalized["claim_valid_at"] == ""
    assert normalized["as_of_date"] == ""
    assert any(
        "actions_plans_investments" in failure
        for failure in dr._normalized_actor_behavior_family_failures(obj)
    )


@pytest.mark.parametrize("missing", ["evidence_type", "confidence", "status", "horizon"])
def test_forward_claims_missing_epistemic_or_temporal_fields_are_not_behavior_ready(
        dr, missing):
    actor = _behavior_ready_actor()
    claim = actor["intelligence"]["dimensions"]["current_actions"][0]
    claim.pop(missing)
    obj = {"actors": [actor]}
    dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[_source()])

    assert any(
        "actions_plans_investments" in failure
        for failure in dr._normalized_actor_behavior_family_failures(obj)
    )


def test_relationships_are_canonical_quote_grounded_and_contract_bound(dr):
    source = _source(names=("Acme", "Beta"))
    acme = _behavior_ready_actor("Acme")
    acme["aliases"] = ["ACME Holdings"]
    beta = _behavior_ready_actor("Beta")
    obj = {
        "actors": [acme, beta],
        "relationships": [
            {
                "source": "ACME Holdings",
                "target": "Beta",
                "type": "PARTNERS_WITH",
                "basis": "Acme and Beta signed a documented partnership agreement.",
                "evidence_type": "verified_fact",
                "claim_valid_at": "2026-07-01",
                "horizon": "current",
                "status": "active",
                "confidence": "high",
                "source_refs": ["https://example.com/actor-plan"],
                "source_support": [_support(
                    "Acme and Beta signed a documented partnership agreement."
                )],
                "qualifiers": {"geography": "North region"},
            },
            {
                "source": "Acme",
                "target": "Beta",
                "type": "OPPOSES",
                "basis": "Unsupported opposition claim.",
                "source_refs": ["https://example.com/actor-plan"],
            },
        ],
    }

    contract = dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[source])

    assert len(obj["relationships"]) == 1
    relation = obj["relationships"][0]
    assert relation["source"] == "Acme"
    assert relation["target"] == "Beta"
    assert relation["source_actor_id"] == acme["actor_id"]
    assert relation["target_actor_id"] == beta["actor_id"]
    assert relation["source_refs"] == [source["source_id"]]
    assert relation["qualifiers"] == {"geography": "North region"}
    assert contract["relationship_count"] == 1
    assert len(contract["relationships_sha256"]) == 64
    assert len(obj["relationship_omission_audit"]) == 1


def test_same_relationship_claim_with_different_causal_attributes_has_distinct_ids(
        dr):
    source = _source(names=("Acme", "Beta"))
    acme = _behavior_ready_actor("Acme")
    beta = _behavior_ready_actor("Beta")
    quote = "Acme and Beta signed a documented partnership agreement."
    base = {
        "source": "Acme",
        "target": "Beta",
        "type": "PARTNERS_WITH",
        "basis": quote,
        "evidence_type": "verified_fact",
        "claim_valid_at": "2026-07-01",
        "horizon": "current",
        "status": "active",
        "confidence": "high",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
        "qualifiers": {"geography": "North region"},
        "valence": " supportive ",
        "polarity": 1,
        "sign": "+",
        "strength": " high ",
        "grade": " A ",
        "since": " 2026-07-01 ",
        "until": " 2027-01-01 ",
    }
    obj = {
        "actors": [acme, beta],
        "relationships": [
            {**base, "lag": " 30   days "},
            {**base, "lag": "90 days"},
        ],
    }

    contract = dr.normalize_actor_intelligence_contract(
        obj, report="r", dossier="d", sources=[source]
    )

    first, second = obj["relationships"]
    assert first["source_actor_id"] == second["source_actor_id"]
    assert first["target_actor_id"] == second["target_actor_id"]
    assert first["basis"] == second["basis"] == quote
    assert first["lag"] == "30 days"
    assert second["lag"] == "90 days"
    assert first["strength"] == second["strength"] == "high"
    assert first["grade"] == second["grade"] == "A"
    assert first["qualifiers"] == second["qualifiers"] == {
        "geography": "North region"
    }
    assert first["claim_sha256"] != second["claim_sha256"]
    assert first["claim_id"] != second["claim_id"]
    assert first["relationship_id"] != second["relationship_id"]
    assert contract["relationship_count"] == 2


def test_duplicate_canonical_relationship_ids_are_rejected(dr):
    source = _source(names=("Acme", "Beta"))
    quote = "Acme and Beta signed a documented partnership agreement."
    relationship = {
        "source": "Acme",
        "target": "Beta",
        "type": "PARTNERS_WITH",
        "basis": quote,
        "evidence_type": "verified_fact",
        "claim_valid_at": "2026-07-01",
        "horizon": "current",
        "status": "active",
        "confidence": "high",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
        "lag": "30 days",
        "sign": "+",
    }
    obj = {
        "actors": [
            _behavior_ready_actor("Acme"),
            _behavior_ready_actor("Beta"),
        ],
        "relationships": [dict(relationship), dict(relationship)],
    }

    with pytest.raises(ValueError, match="duplicate canonical relationship_id"):
        dr.normalize_actor_intelligence_contract(
            obj, report="r", dossier="d", sources=[source]
        )


def test_dossier_actor_claim_projection_rejects_plan_a_vs_plan_b(
        dr, tmp_path):
    source = _source()
    plan_b_quote = "Acme has grounded identity_history Plan B evidence."
    source["excerpt"] += "\n" + plan_b_quote
    source["content_sha256"] = hashlib.sha256(
        source["excerpt"].encode("utf-8")
    ).hexdigest()
    for scope in source["receipt_scopes"]:
        scope["content_sha256"] = source["content_sha256"]
    dossier = _replace_ledger(
        _actor_dossier(dr),
        lambda ledger: ledger["actors"][0]["dimensions"]["identity_history"].update({
            "claims": [{
                **_dimension_claim("Acme", "identity_history"),
                "claim": plan_b_quote,
                "source_support": [_support(plan_b_quote)],
            }],
        }),
    )
    (tmp_path / dr.ACTORS_FILENAME).write_text(
        json.dumps({"actors": [_behavior_ready_actor()]}), encoding="utf-8")
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([source]), encoding="utf-8")
    _write_dossier_coverage_sidecar(
        dr, tmp_path, dossier, [source]
    )
    log = dr.ProgressLog(tmp_path / "projection.log")

    with pytest.raises(
        dr.ActorIntelligenceFinalizationError,
        match="claim projection mismatch",
    ):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=dossier,
            meta={"question": "Q", "depth": "standard", "thread_id": "thread"},
            plog=log,
            required=True,
        )
    log.close()


def test_roster_binding_rejects_order_change_even_when_set_is_identical(
        dr, tmp_path):
    source = _source(names=("Acme", "Beta"))
    (tmp_path / dr.ACTORS_FILENAME).write_text(
        json.dumps({
            "actors": [
                _behavior_ready_actor("Acme"),
                _behavior_ready_actor("Beta"),
            ],
        }),
        encoding="utf-8",
    )
    (tmp_path / dr.SOURCES_FILENAME).write_text(
        json.dumps([source]), encoding="utf-8")
    _write_dossier_coverage_sidecar(
        dr,
        tmp_path,
        _actor_dossier(dr, names=("Beta", "Acme")),
        [source],
    )
    log = dr.ProgressLog(tmp_path / "ordered-roster.log")

    with pytest.raises(
        dr.ActorIntelligenceFinalizationError,
        match="ordered Tier-1/2 roster mismatch",
    ):
        dr.persist_final_actor_intelligence_contract(
            tmp_path,
            report="report",
            dossier=_actor_dossier(dr, names=("Beta", "Acme")),
            meta={"question": "Q", "depth": "standard", "thread_id": "thread"},
            plog=log,
            required=True,
        )
    log.close()


def test_gap_schema_rejects_bare_gap_and_under_attempted_critical_gap(dr):
    bare = _replace_ledger(
        _actor_dossier(dr),
        lambda ledger: ledger["actors"][0]["dimensions"]["future_plans"].update({
            "gap": "No plan found.",
        }),
    )
    bare_audit = dr.actor_dossier_coverage_audit(
        bare,
        [_source()],
        require_source_binding=True,
        search_result_receipts=_dossier_search_result_receipts(dr),
    )
    assert bare_audit["accountable"] is False
    assert any("gap_schema" in error for error in bare_audit["errors"])

    once = _replace_ledger(
        _actor_dossier(dr),
        lambda ledger: ledger["actors"][0]["dimensions"]["future_plans"].update({
            "gap": _gap("future_plans", attempts=1),
        }),
    )
    once_audit = dr.actor_dossier_coverage_audit(
        once,
        [_source()],
        require_source_binding=True,
        search_result_receipts=_dossier_search_result_receipts(dr),
    )
    assert once_audit["accountable"] is False
    assert any("critical_gap_attempts_lt_2" in error for error in once_audit["errors"])


def test_track_b_audit_cannot_spend_track_a_only_receipts(dr):
    audit = dr.actor_dossier_coverage_audit(
        _actor_dossier(dr),
        [_source(purpose="research:deep-opening")],
        require_source_binding=True,
        required_receipt_purpose="track-b",
        search_result_receipts=_dossier_search_result_receipts(dr),
    )
    assert audit["accountable"] is False
    assert any(
        "covered_without_track_b_receipt" in error
        for error in audit["errors"]
    )


@pytest.mark.parametrize(
    "shape_key",
    [
        "claim",
        "summary",
        "fact",
        "detail",
        "description",
        "plan",
        "action",
        "investment",
        "decision",
        "preference",
        "value",
    ],
)
def test_actor_claim_parser_accepts_every_documented_shape_after_triangulation(
        dr, shape_key):
    quote = "Acme plans a conditional capacity expansion subject to permit approval."
    value = {
        shape_key: "Plans a conditional capacity expansion.",
        "evidence_type": "actor_stated_claim",
        "claim_valid_at": "2026-07-01",
        "horizon": "2027",
        "status": "proposed",
        "confidence": "medium",
        "source_refs": ["https://example.com/actor-plan"],
        "source_support": [_support(quote)],
    }
    source = _source()
    claim = dr._normalize_intelligence_claim(
        value,
        dr._canonical_source_lookup([source]),
        "2099-01-01",
    )
    assert claim is not None
    assert claim["claim"] == "Plans a conditional capacity expansion."
