"""RX00: real offline producer sealing must interoperate with context consumers.

Only fixture evidence is used. The producer assigns IDs and computes its own
hashes; no test recreates the current roster-hash algorithm to bless its output.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.services.actor_context import (
    build_actor_context_artifacts,
    build_actor_context_pack,
    canonical_json_sha256,
    validate_actor_context_artifacts,
)


@pytest.fixture(scope="module")
def sealed_research(tmp_path_factory):
    bridge_path = Path(__file__).resolve().parents[2] / "deerflow_bridge/deerflow_research.py"
    spec = importlib.util.spec_from_file_location("roster_boundary_producer", bridge_path)
    producer = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(producer)

    source_url = "https://example.com/roster-evidence"
    actors = []
    ledger_actors = []
    profiles = []
    quotes = []
    for name in ("Alpha Systems", "Beta Union"):
        dimensions = {}
        for dimension in producer.ACTOR_INTELLIGENCE_DIMENSIONS:
            quote = f"{name} has documented {dimension} evidence."
            quotes.append(quote)
            claim = {
                "claim": quote,
                "evidence_type": "verified_fact",
                "claim_valid_at": "2026-07-01",
                "horizon": "current",
                "status": "observed",
                "confidence": "high",
                "source_refs": [source_url],
                "source_support": [{
                    "source_ref": source_url,
                    "supporting_quote": quote,
                }],
            }
            if dimension == "knowledge_state":
                claim["evidence_type"] = "contested"
                claim["qualifiers"] = {"actor_knows": False}
            dimensions[dimension] = [claim]
        actors.append({
            "name": name,
            "type": "company",
            "simulation_tier": 1,
            "intelligence": {
                "schema_version": "actor-intelligence/v1",
                "dimensions": dimensions,
                "evidence_gaps": {},
            },
        })
        ledger_actors.append({
            "name": name,
            "simulation_tier": 1,
            "dimensions": {
                dimension: {
                    "status": "covered",
                    "source_refs": [source_url],
                    "claims": claims,
                    "gap": None,
                }
                for dimension, claims in dimensions.items()
            },
        })
        profiles.append(
            f"### Actor: {name}\n\n"
            + "\n".join(claims[0]["claim"] for claims in dimensions.values())
        )

    report = "# Research report\n\n" + "\n\n".join(profiles)
    dossier_text = (
        "# Actor dossier\n\n" + "\n\n".join(profiles)
        + "\n\n<!-- ACTOR_INTELLIGENCE_LEDGER_V1\n"
        + json.dumps({
            "schema_version": producer.ACTOR_INTELLIGENCE_SCHEMA_VERSION,
            "actors": ledger_actors,
        }) + "\n-->\n"
    )
    excerpt = "\n".join(quotes)
    content_sha = hashlib.sha256(excerpt.encode()).hexdigest()
    source = {
        "url": source_url,
        "title": "Offline roster evidence",
        "tier": "S1",
        "publication_date": "2026-06-30",
        "source_origin": "fetched",
        "reachable": True,
        "content_sha256": content_sha,
        "excerpt": excerpt,
        "receipt_id": "receipt_roster_fixture",
        "provider": "offline-fixture",
        "receipt_scopes": [{
            "thread_id": "roster-fixture-thread",
            "lane": "track-b",
            "purpose": "actor-ontology",
            "receipt_id": "receipt_roster_fixture",
            "content_sha256": content_sha,
        }],
    }
    research_dir = tmp_path_factory.mktemp("sealed-roster-research")
    actors_path = research_dir / producer.ACTORS_FILENAME
    actors_path.write_text(json.dumps({
        "as_of_date": "2026-07-01",
        "actors": actors,
    }), encoding="utf-8")
    (research_dir / producer.SOURCES_FILENAME).write_text(
        json.dumps([source]), encoding="utf-8"
    )
    log = producer.ProgressLog(research_dir / "progress.log")
    try:
        contract = producer.persist_final_actor_intelligence_contract(
            research_dir,
            report=report,
            dossier=dossier_text,
            meta={"question": "Which organization can act?"},
            plog=log,
        )
    finally:
        log.close()
    assert contract is not None
    artifact_bytes = actors_path.read_bytes()
    sealed = json.loads(artifact_bytes)
    assert sealed["actor_intelligence_contract"] == contract
    assert contract["actor_ids_sha256"] == contract["actor_ids_multiset_sha256"]
    assert contract["claim_projection_count"] == 2 * len(producer.ACTOR_INTELLIGENCE_DIMENSIONS)
    return sealed, report, actors_path, artifact_bytes


@pytest.fixture
def research(sealed_research):
    sealed, report, _path, _bytes = sealed_research
    return copy.deepcopy(sealed), report


def _legacy_roster_hash(actors):
    """Documented historical v1 encoding, intentionally not the producer's current format."""
    return hashlib.sha256(
        "\n".join(sorted(row["actor_id"] for row in actors)).encode("utf-8")
    ).hexdigest()


def _as_legacy(sealed):
    contract = sealed["actor_intelligence_contract"]
    del contract["actor_ids_multiset_sha256"]
    del contract["actor_ids_ordered_sha256"]
    contract["actor_ids_sha256"] = _legacy_roster_hash(sealed["actors"])


def test_real_sealed_producer_artifact_reaches_context_manifest(sealed_research, tmp_path):
    sealed, report, actors_path, artifact_bytes = sealed_research
    original = copy.deepcopy(sealed)
    selected = sealed["actors"]
    packs, manifest, manifest_sha = build_actor_context_artifacts(
        str(tmp_path), sealed, selected, report
    )
    restored_manifest, restored_packs = validate_actor_context_artifacts(
        str(tmp_path),
        expected_count=len(selected),
        expected_manifest_sha256=manifest_sha,
        expected_report_sha256=sealed["actor_intelligence_contract"]["report_sha256"],
        expected_actors_sha256=canonical_json_sha256(sealed),
        expected_actor_ids=[row["actor_id"] for row in selected],
    )
    assert restored_manifest == manifest
    assert restored_packs == packs
    for actor in selected:
        pack = packs[actor["actor_id"]]
        assert pack["source"]["actor_ids_sha256"] == sealed["actor_intelligence_contract"]["actor_ids_sha256"]
        assert pack["dimension_coverage"]["missing_dimensions"] == []
        assert pack["actor_intelligence"] == actor["intelligence"]
        assert "knowledge_state" not in pack["epistemic_context"]["documented_actor_beliefs_and_knowledge"]
    assert sealed == original
    assert actors_path.read_bytes() == artifact_bytes


def test_explicit_legacy_v1_roster_remains_readable_and_unordered(research):
    sealed, report = research
    _as_legacy(sealed)
    sealed["actors"].reverse()
    assert build_actor_context_pack(sealed, sealed["actors"][0], report)


@pytest.mark.parametrize("field", [
    "actor_ids_sha256", "actor_ids_multiset_sha256", "actor_ids_ordered_sha256",
])
def test_current_roster_rejects_each_mismatched_digest(research, field):
    sealed, report = research
    sealed["actor_intelligence_contract"][field] = "0" * 64
    with pytest.raises(ValueError, match="roster fingerprint"):
        build_actor_context_pack(sealed, sealed["actors"][0], report)


def test_legacy_hash_cannot_override_current_multiset_fields(research):
    sealed, report = research
    sealed["actor_intelligence_contract"]["actor_ids_sha256"] = _legacy_roster_hash(sealed["actors"])
    with pytest.raises(ValueError, match="roster fingerprint"):
        build_actor_context_pack(sealed, sealed["actors"][0], report)


@pytest.mark.parametrize("field", ["actor_ids_multiset_sha256", "actor_ids_ordered_sha256"])
@pytest.mark.parametrize("value", [None, "", "not-a-digest", "missing"])
def test_partial_current_roster_cannot_downgrade_to_legacy(research, field, value):
    sealed, report = research
    contract = sealed["actor_intelligence_contract"]
    # The old digest would pass if a malformed current field selected legacy mode.
    contract["actor_ids_sha256"] = _legacy_roster_hash(sealed["actors"])
    if value == "missing":
        del contract[field]
    else:
        contract[field] = value
    with pytest.raises(ValueError, match="roster fingerprint"):
        build_actor_context_pack(sealed, sealed["actors"][0], report)


@pytest.mark.parametrize("legacy", [False, True])
def test_duplicate_actor_ids_fail_before_roster_hash_acceptance(research, legacy):
    sealed, report = research
    if legacy:
        _as_legacy(sealed)
    sealed["actors"].append(copy.deepcopy(sealed["actors"][0]))
    sealed["actor_intelligence_contract"]["actor_count"] = len(sealed["actors"])
    if legacy:
        sealed["actor_intelligence_contract"]["actor_ids_sha256"] = _legacy_roster_hash(sealed["actors"])
    with pytest.raises(ValueError, match="duplicate actor identities"):
        build_actor_context_pack(sealed, sealed["actors"][0], report)


@pytest.mark.parametrize("change", ["reorder", "replace", "remove-current-markers"])
def test_current_roster_tampering_is_rejected(research, change):
    sealed, report = research
    if change == "reorder":
        sealed["actors"].reverse()
    elif change == "replace":
        sealed["actors"][0]["actor_id"] = "actor_unbound_replacement"
    else:
        del sealed["actor_intelligence_contract"]["actor_ids_multiset_sha256"]
        del sealed["actor_intelligence_contract"]["actor_ids_ordered_sha256"]
    with pytest.raises(ValueError, match="roster fingerprint"):
        build_actor_context_pack(sealed, sealed["actors"][0], report)
