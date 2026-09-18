"""Pinned native output policy across actual parent/child/factory/SDK boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils import simulation_usage as su
from app.utils import telemetry as tel
from app.utils.oasis_output_policy import model_output_policy
from app.utils.usage_ledger import UsageLedger, UsageLedgerConflict


@pytest.fixture(params=["max_tokens", "max_completion_tokens"])
def launch(tmp_path, monkeypatch, request):
    previous = tel.get_run_context()
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 2000)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 32)
    monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", request.param)
    monkeypatch.setattr(su, "_context", None)
    state = po.PipelineState(pipeline_id="pipe_native_cap", prompt="offline", task_id="task-native-cap")
    tel.LLMMeter.reset(state.pipeline_id)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    sim_id = "sim-native-cap"
    config = tmp_path / "simulations" / sim_id / "simulation_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"simulation_id": sim_id}), encoding="utf-8")
    context = orch._simulation_usage_context(state, sim_id)
    yield SimpleNamespace(state=state, orch=orch, config=config, context=context,
                          ledger=Path(context["ledger_path"]), sim_id=sim_id)
    tel.LLMMeter.reset(state.pipeline_id)
    tel.set_run_context(*previous)


_CHILD = textwrap.dedent("""\
    import asyncio, json, os, sys
    import httpx
    from openai import OpenAI, AsyncOpenAI
    from app.config import Config
    imported = [Config.OASIS_MAX_OUTPUT_TOKENS, Config.OASIS_OUTPUT_TOKEN_PARAMETER]
    from app.utils import simulation_usage as su, telemetry as tel
    try:
        authority = su.bootstrap(sys.argv[1])
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "provider_calls": 0}))
        sys.exit(17)
    policy = su.current_output_policy()
    # A later configuration/environment mutation must not alter this launch.
    Config.OASIS_MAX_OUTPUT_TOKENS = 1
    Config.OASIS_OUTPUT_TOKEN_PARAMETER = "invalid-after-bootstrap"
    os.environ["OASIS_MAX_OUTPUT_TOKENS"] = "2"
    if sys.argv[2] == "bootstrap":
        from app.utils.oasis_output_policy import model_output_policy
        print(json.dumps({"imported": imported, "authority": authority,
                          "policy": model_output_policy()}))
        sys.exit(0)
    from camel.models import openai_model
    from app.utils.oasis_llm import create_oasis_model
    clients, seen = [], []
    def transport(request):
        before = tel.LLMMeter.cumulative_snapshot(authority["run_id"])
        seen.append({"body": json.loads(request.content),
                     "held_before_response": before["token_reservation_state"]["reserved_tokens"]})
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 1,
            "model": "gpt-4o-mini", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "accepted"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    def sync_client(*args, **kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(transport))
        client = OpenAI(*args, **kwargs)
        clients.append(client)
        return client
    def async_client(*args, **kwargs):
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        client = AsyncOpenAI(*args, **kwargs)
        clients.append(client)
        return client
    openai_model.OpenAI, openai_model.AsyncOpenAI = sync_client, async_client
    messages = [{"role": "user", "content": "offline action"}]
    try:
        primary = create_oasis_model({})
        boosted = create_oasis_model({}, use_boost=True)
        assert "max_tokens" not in primary.model_config_dict
        assert primary._request_chat_completion(messages).choices[0].message.content == "accepted"
        async def invoke():
            return await boosted._arequest_chat_completion(messages)
        assert asyncio.run(invoke()).choices[0].message.content == "accepted"
        su.assert_complete()
        print(json.dumps({"imported": imported, "authority": authority, "policy": policy, "seen": seen}))
    finally:
        for client in clients:
            if isinstance(client, AsyncOpenAI):
                asyncio.run(client.close())
            else:
                client.close()
""")


def _environment(launch):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "OASIS_", "OPENAI_"))}
    env.update(LLM_PROVIDER="openai", LLM_API_KEY="offline-fixture",
               LLM_MODEL_NAME="gpt-4o-mini", LLM_BASE_URL="https://offline.invalid/v1",
               LLM_BOOST_API_KEY="offline-boost", LLM_BOOST_MODEL_NAME="gpt-4o-mini",
               LLM_BOOST_BASE_URL="https://offline-boost.invalid/v1", LLM_BOOST_PROVIDER="openai",
               OASIS_MAX_OUTPUT_TOKENS="bad-ambient", OASIS_OUTPUT_TOKEN_PARAMETER="bad-ambient",
               SIM_LLM_FALLBACK="false", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return su.child_environment(env, launch.context, str(launch.config))


def _child(launch, scenario="bootstrap", env=None):
    process = subprocess.run([sys.executable, "-c", _CHILD, str(launch.config), scenario],
                             env=_environment(launch) if env is None else env,
                             cwd=launch.config.parent, capture_output=True, text=True, timeout=30)
    result = json.loads(process.stdout.strip().splitlines()[-1]) if process.stdout.strip() else None
    return process, result


def test_parent_child_factory_native_and_boost_share_pinned_cap_and_settle(launch):
    before = hashlib.sha256(launch.config.read_bytes()).hexdigest()
    process, result = _child(launch, "dispatch")
    assert process.returncode == 0, process.stderr + process.stdout
    policy = launch.context["native_output_policy"]
    assert result["imported"] == [32, policy["parameter"]]
    assert result["authority"] == su.authority(launch.context)
    assert len(result["seen"]) == 2
    opposite = "max_completion_tokens" if policy["parameter"] == "max_tokens" else "max_tokens"
    for sent in result["seen"]:
        assert sent["body"][policy["parameter"]] == 32
        assert opposite not in sent["body"]
        assert sent["held_before_response"] > 32
    snapshot = UsageLedger(launch.ledger).snapshot(launch.state.pipeline_id)
    assert snapshot["total"]["total_tokens"] == 10
    assert snapshot["total"]["calls"] == 2
    assert snapshot["token_reservation_state"]["reserved_tokens"] == 0
    assert hashlib.sha256(launch.config.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("change", ["remove", "cap", "parameter", "tokens", "usd", "null"])
def test_child_rejects_policy_or_budget_tamper_before_provider_work(launch, change):
    env = _environment(launch)
    payload = json.loads(env[su.CONTEXT_ENV])
    if change == "remove":
        payload.pop("native_output_policy")
    elif change == "cap":
        payload["native_output_policy"]["max_output_tokens"] += 1
    elif change == "parameter":
        payload["native_output_policy"]["parameter"] = (
            "max_completion_tokens" if Config.OASIS_OUTPUT_TOKEN_PARAMETER == "max_tokens" else "max_tokens")
    elif change == "null":
        payload["native_output_policy"] = None
    else:
        payload["budget_tokens" if change == "tokens" else "budget_usd"] += 1
    env[su.CONTEXT_ENV] = json.dumps(payload)
    process, result = _child(launch, "dispatch", env)
    assert process.returncode == 17, process.stderr + process.stdout
    assert result == {"error": "UsageLedgerConflict", "provider_calls": 0}
    assert UsageLedger(launch.ledger).snapshot(launch.state.pipeline_id)["total"]["calls"] == 0


def test_bound_policy_and_authority_are_independent_copies(launch, monkeypatch):
    env = _environment(launch)
    monkeypatch.setenv(su.CONTEXT_ENV, env[su.CONTEXT_ENV])
    tel.LLMMeter.reset(launch.state.pipeline_id)  # Simulate the child's fresh process scope.
    su.bootstrap(str(launch.config))
    expected = copy.deepcopy(launch.context["native_output_policy"])
    su.current_authority()["native_output_policy"]["max_output_tokens"] = 99
    su.current_output_policy()["max_output_tokens"] = 100
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 101)
    monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", "invalid")
    assert model_output_policy() == expected
    saved = launch.state.options["simulation_usage_launches"][launch.context["launch_token"]]
    launch.context["native_output_policy"]["max_output_tokens"] = 102
    assert saved["native_output_policy"] == expected


@pytest.mark.parametrize("change", ["cap", "parameter", "saved_invalid"])
def test_resume_cannot_change_policy_and_rejection_does_not_save_launch(launch, monkeypatch, change):
    if change == "cap":
        monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 64)
    elif change == "parameter":
        monkeypatch.setattr(Config, "OASIS_OUTPUT_TOKEN_PARAMETER", (
            "max_completion_tokens" if Config.OASIS_OUTPUT_TOKEN_PARAMETER == "max_tokens" else "max_tokens"))
    else:
        launch.state.options["simulation_usage_launches"][launch.context["launch_token"]]["native_output_policy"] = None
        po.PipelineManager.save(launch.state)
    before = Path(launch.context["pipeline_state_path"]).read_bytes()
    reloaded = po.PipelineState.from_dict(po.PipelineManager.load(launch.state.pipeline_id))
    with pytest.raises(UsageLedgerConflict):
        po.PipelineOrchestrator()._simulation_usage_context(reloaded, launch.sim_id)
    assert len(reloaded.options["simulation_usage_launches"]) == 1
    assert Path(launch.context["pipeline_state_path"]).read_bytes() == before


def test_matching_resume_and_new_simulation_can_launch(launch, monkeypatch):
    reloaded = po.PipelineState.from_dict(po.PipelineManager.load(launch.state.pipeline_id))
    orch = po.PipelineOrchestrator()
    resumed = orch._simulation_usage_context(reloaded, launch.sim_id)
    assert resumed["launch_token"] != launch.context["launch_token"]
    assert resumed["native_output_policy"] == launch.context["native_output_policy"]
    monkeypatch.setattr(Config, "OASIS_MAX_OUTPUT_TOKENS", 64)
    other = orch._simulation_usage_context(reloaded, "sim-other")
    assert other["native_output_policy"]["max_output_tokens"] == 64


def test_legacy_bound_launch_stays_uncapped_and_cannot_inject_new_policy(launch):
    launch.context.pop("native_output_policy")
    launch.state.options["simulation_usage_launches"][launch.context["launch_token"]] = su.authority(launch.context)
    po.PipelineManager.save(launch.state)
    process, result = _child(launch)
    assert process.returncode == 0, process.stderr + process.stdout
    assert result["imported"] == [0, "max_tokens"]
    assert result["policy"]["max_output_tokens"] == 0
    env = _environment(launch)
    payload = json.loads(env[su.CONTEXT_ENV])
    payload["native_output_policy"] = {"schema": "oasis-output-policy/v1", "max_output_tokens": 32,
                                       "parameter": Config.OASIS_OUTPUT_TOKEN_PARAMETER}
    env[su.CONTEXT_ENV] = json.dumps(payload)
    rejected, error = _child(launch, env=env)
    assert rejected.returncode == 17
    assert error["error"] == "UsageLedgerConflict"
    # The first explicitly configured new launch of legacy history captures it.
    current = launch.orch._simulation_usage_context(launch.state, launch.sim_id)
    assert current["native_output_policy"] == payload["native_output_policy"]


def test_unbound_environment_preserves_standalone_configuration(launch):
    original = {su.CONTEXT_ENV: "unrelated", "OASIS_MAX_OUTPUT_TOKENS": "7"}
    assert su.child_environment(original, None, str(launch.config)) == {"OASIS_MAX_OUTPUT_TOKENS": "7"}
    assert original[su.CONTEXT_ENV] == "unrelated"


def test_output_policy_leaves_actor_config_and_direct_child_seals_valid(launch):
    from test_simulation_config_seal import _sealed_simulation
    from app.services.simulation_manager import validate_simulation_config_seal
    from scripts.run_parallel_simulation import validate_direct_child_config_seal

    directory = launch.config.parent.parent / "sim_config_seal"
    directory.mkdir()
    config, config_sha, manifest_sha = _sealed_simulation(directory)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    launch.config = config
    launch.sim_id = "sim_config_seal"
    launch.context = launch.orch._simulation_usage_context(launch.state, launch.sim_id)
    process, result = _child(launch, "dispatch")
    assert process.returncode == 0, process.stderr + process.stdout
    assert len(result["seen"]) == 2
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    assert after == before
    manifest = validate_simulation_config_seal(str(directory), expected_manifest_sha256=manifest_sha,
                                              expected_config_sha256=config_sha,
                                              expected_simulation_id=launch.sim_id, require=True)
    assert manifest["simulation_config_sha256"] == config_sha
    assert validate_direct_child_config_seal(str(config), manifest_sha)["manifest_sha256"] == manifest_sha
