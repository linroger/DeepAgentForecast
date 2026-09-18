"""Explicit actor access denial survives every shared-context projection."""

from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from app.config import Config
from app.services.actor_context import (
    build_actor_context_artifacts,
    canonical_json_sha256,
    validate_actor_context_artifacts,
)
from app.services.actor_role_prompt import build_actor_role_contract
from scripts.run_parallel_simulation import _inject_world_brief
from test_actor_context_runtime import REPORT, _dossier, _entity
from test_sim_config_v1_provenance import _Graph, _generator


ACCESS_MARKER = "Alpha access-controlled permit outcome"
PUBLIC_CONTROL = "Beta publicly announced member timetable"


@pytest.mark.parametrize("visibility_location", ["claim", "qualifiers"])
@pytest.mark.parametrize(
    "access,denied",
    [
        pytest.param({"actor_knows": False}, True, id="claim-denial"),
        pytest.param({"qualifiers": {"actor_knows": False}}, True, id="qualifier-denial"),
        pytest.param(
            {"actor_knows": False, "qualifiers": {"actor_knows": True}},
            True,
            id="claim-denial-overrides-qualifier-grant",
        ),
        pytest.param(
            {"actor_knows": True, "qualifiers": {"actor_knows": False}},
            True,
            id="qualifier-denial-overrides-claim-grant",
        ),
        pytest.param({}, False, id="public-without-denial"),
        pytest.param({"actor_knows": True}, False, id="public-with-explicit-grant"),
    ],
)
def test_access_policy_survives_sealed_context_and_shared_consumers(
    tmp_path, monkeypatch, visibility_location: str, access: dict, denied: bool
) -> None:
    monkeypatch.setattr(Config, "SIM_WORLD_BRIEF", True)
    monkeypatch.setattr(Config, "ADAPTIVE_CONTEXT", False)
    monkeypatch.setattr(Config, "SIM_SYNTH_SEED_POSTS", True)
    dossier = _dossier()
    alpha, beta = dossier["actors"]
    alpha_claim = alpha["intelligence"]["dimensions"]["knowledge_state"][0]
    alpha_claim["claim"] = ACCESS_MARKER
    alpha_claim["qualifiers"] = {}
    alpha_claim.update(deepcopy(access))
    if visibility_location == "claim":
        alpha_claim["visibility"] = "public"
    else:
        alpha_claim["qualifiers"]["visibility"] = "public"
    beta_claim = beta["intelligence"]["dimensions"]["knowledge_state"][0]
    beta_claim["claim"] = PUBLIC_CONTROL
    beta_claim["visibility"] = "public"

    packs, manifest, manifest_sha = build_actor_context_artifacts(
        str(tmp_path), dossier, [alpha, beta], REPORT
    )
    _, persisted_packs = validate_actor_context_artifacts(
        str(tmp_path),
        expected_count=2,
        expected_manifest_sha256=manifest_sha,
        expected_report_sha256=manifest["report_sha256"],
        expected_actors_sha256=manifest["actors_sha256"],
    )
    assert persisted_packs == packs
    before = canonical_json_sha256(packs)
    epistemic = packs["actor_alpha"]["epistemic_context"]
    actor_known = json.dumps(epistemic["documented_actor_beliefs_and_knowledge"])
    # Public availability alone is not an actor-local knowledge grant. The
    # shared brief may introduce public evidence only when no denial exists.
    explicitly_granted = (
        access.get("actor_knows") is True
        or access.get("qualifiers", {}).get("actor_knows") is True
    )
    assert (ACCESS_MARKER in actor_known) is (explicitly_granted and not denied)
    if denied:
        # Keep the documented observation available for modeler audit without
        # upgrading it to this actor's knowledge through another channel.
        audit = epistemic["documented_actor_evidence_not_automatically_actor_knowledge"]
        assert ACCESS_MARKER in json.dumps(audit)
        role = build_actor_role_contract(alpha, dossier, packs["actor_alpha"])
        assert ACCESS_MARKER not in role["known_context"]
        assert ACCESS_MARKER in json.dumps(role["report_context"])

    channels = _shared_channels(dossier, packs)
    assert all(PUBLIC_CONTROL in content for content in channels.values())
    observed = {name: ACCESS_MARKER in content for name, content in channels.items()}
    assert observed == dict.fromkeys(channels, not denied)
    assert canonical_json_sha256(packs) == before


def _shared_channels(dossier: dict, packs: dict) -> dict[str, str]:
    generator = _generator()
    entities = [_entity(actor) for actor in dossier["actors"]]
    question = "Will the grid permit be approved?"
    brief = generator._build_world_brief(
        question, dossier, [], actor_context_packs=packs
    )
    context = generator._build_context(
        question, REPORT, entities, actors=dossier, actor_context_packs=packs
    )
    event_prompts = []

    def capture_event_prompt(prompt: str, system_prompt: str) -> dict:
        event_prompts.append(prompt)
        return {"initial_posts": [], "hot_topics": [], "reasoning": "offline"}

    generator._call_llm_with_retry = capture_event_prompt
    event_result = generator._generate_event_config(
        context, question, entities, actors=dossier, actor_context_packs=packs
    )
    event = generator._parse_event_config(
        event_result, actors=dossier, actor_context_packs=packs
    )
    assert len(event_prompts) == 1
    assert event.initial_posts
    graph = _Graph()
    _inject_world_brief(graph, brief, lambda message: None)
    channels = {
        "world_brief": brief,
        "config_context": context,
        "event_prompt": event_prompts[0],
        "seed_posts": json.dumps(asdict(event)),
        **{
            f"agent_{agent_id}": agent._system_message.content
            for agent_id, agent in graph.get_agents()
        },
    }
    return channels


@pytest.mark.parametrize("public_first", [False, True], ids=["denial-first", "public-first"])
@pytest.mark.parametrize("duplicate_dimension", ["knowledge_state", "identity_history"])
def test_denial_prevents_duplicate_claim_from_another_actor_becoming_shared(
    tmp_path, monkeypatch, public_first: bool, duplicate_dimension: str
) -> None:
    monkeypatch.setattr(Config, "SIM_WORLD_BRIEF", True)
    monkeypatch.setattr(Config, "ADAPTIVE_CONTEXT", False)
    monkeypatch.setattr(Config, "SIM_SYNTH_SEED_POSTS", True)
    dossier = _dossier()
    alpha, beta = dossier["actors"]
    denied_claim = alpha["intelligence"]["dimensions"]["knowledge_state"][0]
    denied_claim["claim"] = ACCESS_MARKER
    denied_claim["visibility"] = "public"
    denied_claim["qualifiers"]["actor_knows"] = False
    allowed_claim = deepcopy(denied_claim)
    # Exercise the same case-insensitive identity as shared-row deduplication.
    allowed_claim["claim"] = ACCESS_MARKER.upper()
    allowed_claim["qualifiers"]["actor_knows"] = True
    beta["intelligence"]["dimensions"][duplicate_dimension].append(allowed_claim)
    control = beta["intelligence"]["dimensions"]["knowledge_state"][0]
    control["claim"] = PUBLIC_CONTROL
    control["visibility"] = "public"
    if public_first:
        dossier["actors"] = [beta, alpha]
    packs, _, manifest_sha = build_actor_context_artifacts(
        str(tmp_path), dossier, dossier["actors"], REPORT
    )
    before = canonical_json_sha256(packs)
    alpha_knowledge = packs["actor_alpha"]["epistemic_context"]
    assert ACCESS_MARKER not in json.dumps(
        alpha_knowledge["documented_actor_beliefs_and_knowledge"]
    )
    assert ACCESS_MARKER in json.dumps(
        alpha_knowledge["documented_actor_evidence_not_automatically_actor_knowledge"]
    )
    beta_knowledge = packs["actor_beta"]["epistemic_context"]
    assert ACCESS_MARKER.upper() in json.dumps(
        beta_knowledge["documented_actor_beliefs_and_knowledge"]
    )
    channels = _shared_channels(dossier, packs)
    assert all(PUBLIC_CONTROL in content for content in channels.values())
    observed = {
        name: ACCESS_MARKER.casefold() in content.casefold()
        for name, content in channels.items()
    }
    assert not any(observed.values()), observed
    assert canonical_json_sha256(packs) == before
    _, persisted = validate_actor_context_artifacts(
        str(tmp_path), expected_count=2, expected_manifest_sha256=manifest_sha
    )
    assert persisted == packs
