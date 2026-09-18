"""Detached child accounting survives process boundaries without aggregate replay.

Every subprocess uses a real SDK with an offline MockTransport, or a mocked CLI
method. No provider, simulation engine, or network service is started.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import textwrap
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils import simulation_usage as su
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedger, UsageLedgerConflict, UsageLedgerUnresolvedError


@pytest.fixture
def launch(tmp_path, monkeypatch):
    previous = tel.get_run_context()
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"))
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_USD", 0)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", False)
    monkeypatch.setattr(su, "_context", None)
    state = po.PipelineState(pipeline_id="pipe_shared_child", prompt="offline", task_id="task-child")
    tel.LLMMeter.reset(state.pipeline_id)
    orch = po.PipelineOrchestrator()
    orch._init_telemetry_flush(state)
    sim_id = "sim_shared_child"
    config = tmp_path / "simulations" / sim_id / "simulation_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"simulation_id": sim_id}), encoding="utf-8")
    context = orch._simulation_usage_context(state, sim_id)
    yield SimpleNamespace(state=state, orch=orch, config=config, context=context,
                          ledger=Path(context["ledger_path"]), sim_id=sim_id)
    tel.LLMMeter.reset(state.pipeline_id)
    tel.set_run_context(*previous)


_CHILD = textwrap.dedent("""\
    import json, os, sys, threading
    from concurrent.futures import ThreadPoolExecutor
    import httpx
    from openai import OpenAI
    from app.config import Config
    from app.utils import simulation_usage as su
    from app.utils import telemetry as tel
    from app.utils import llm_client as lc

    scenario = sys.argv[2]
    try:
        auth = su.bootstrap(sys.argv[1])
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "provider_calls": 0}))
        sys.exit(17)
    if scenario == "bootstrap":
        print(json.dumps({"authority": auth}))
        sys.exit(0)
    if scenario in {"complete", "own_pending"}:
        if scenario == "own_pending":
            operation_id = tel.LLMMeter.new_operation_id(auth["run_id"])
            tel.LLMMeter.record_snapshot("llm_api_attempt", operation_id, "openai", "fixture-model",
                0, 0, 0.0, calls=0, status="in_flight", usage_source="unknown")
        try:
            su.assert_complete()
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "provider_calls": 0}))
            sys.exit(19)
        print(json.dumps({"complete": True}))
        sys.exit(0)
    Config.LLM_TELEMETRY_ENABLED = False
    Config.LLM_CACHE_ENABLED = False
    Config.LLM_TIERED_ROUTING = False
    os.environ["LLM_FALLBACK_PROVIDER"] = ""
    lc._CB_STATE = {}
    lc.time.sleep = lambda _: None
    seen = []
    lock = threading.Lock()
    barrier = threading.Barrier(2) if scenario == "pool" else None

    def transport(request):
        with lock:
            seen.append(True)
            number = len(seen)
        if scenario == "crash":
            os._exit(23)
        if barrier is not None:
            barrier.wait(timeout=10)
        if scenario == "retry" and number == 1:
            return httpx.Response(429, json={"error": {"message": "offline limit", "type": "rate_limit"}})
        empty = scenario == "retry" and number == 2
        pt, ct = (100, 20) if empty else (50, 10)
        body = {"id": "fixture", "object": "chat.completion", "created": 1,
                "model": "fixture-model", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "" if empty else "accepted"}}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt+ct}}
        return httpx.Response(200, json=body)

    if scenario == "cli":
        lc.LLMClient._chat_claude_cli = lambda *args, **kwargs: "accepted"
        client = lc.LLMClient(provider="claude-cli", model="fixture-cli")
        assert client.chat([{"role": "user", "content": "offline fixture prompt"}]) == "accepted"
    else:
        with OpenAI(api_key="fixture-only", base_url="https://offline.invalid/v1",
                    max_retries=2, http_client=httpx.Client(transport=httpx.MockTransport(transport))) as sdk:
            lc.LLMClient._build_openai_client = staticmethod(lambda *args: sdk)
            client = lc.LLMClient(provider="openai", model="fixture-model", api_key="fixture-only")
            if barrier is not None:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    assert list(pool.map(lambda _: client.chat([]), range(2))) == ["accepted", "accepted"]
            else:
                assert client.chat([]) == "accepted"
    su.assert_complete()
    print(json.dumps({"authority": auth, "provider_calls": len(seen)}))
""")


def _environment(launch):
    # Preserve runtime import paths, but never inherit operational LLM settings.
    env = {key: value for key, value in os.environ.items() if not key.startswith("LLM_")}
    env.update(LLM_PROVIDER="openai", LLM_API_KEY="fixture-only", LLM_MODEL_NAME="fixture-model")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return su.child_environment(env, launch.context, str(launch.config))


def _child(launch, scenario="bootstrap", env=None):
    return subprocess.run([sys.executable, "-c", _CHILD, str(launch.config), scenario],
                          env=env if env is not None else _environment(launch),
                          cwd=launch.config.parent, capture_output=True, text=True, timeout=30)


def _last_json(process):
    return json.loads(process.stdout.strip().splitlines()[-1])


def _snapshot(launch):
    return tel.LLMMeter.cumulative_snapshot(launch.state.pipeline_id)


def _payload(launch, **overrides):
    payload = {"simulation_id": launch.sim_id, "meter_run_token": launch.context["launch_token"],
               "usage_authority": su.authority(launch.context), "provider": "openai", "model": "fixture-model",
               "calls": 100, "prompt_tokens": 9000, "completion_tokens": 1000,
               "total_tokens": 10000, "wall_s": 1}
    payload.update(overrides)
    return payload


def test_detached_retry_and_empty_response_persist_without_json_flush(launch):
    process = _child(launch, "retry")
    assert process.returncode == 0, process.stderr
    assert _last_json(process)["provider_calls"] == 3
    snap = _snapshot(launch)
    assert snap["total"]["calls"] == 3
    assert snap["total"]["total_tokens"] == 180
    assert snap["usage_by_class"]["unknown"]["calls"] == 1
    assert snap["by_stage"]["run"]["total_tokens"] == 180
    assert not (launch.config.parent / "sim_llm_telemetry.json").exists()
    with sqlite3.connect(launch.ledger) as connection:
        rows = connection.execute("SELECT owner_attempt_id, status FROM usage_operations").fetchall()
    assert len(rows) == 3
    assert {row[0] for row in rows} == {launch.context["attempt_id"]}
    assert sorted(row[1] for row in rows) == ["completed", "completed", "unknown"]


def test_abrupt_child_exit_leaves_marker_and_blocks_new_parent_attempt(launch):
    process = _child(launch, "crash")
    assert process.returncode == 23
    snap = _snapshot(launch)
    assert snap["total"]["calls"] == 0
    assert snap["api_operation_state"]["in_flight"] == 1
    assert not (launch.config.parent / "sim_llm_telemetry.json").exists()
    tel.LLMMeter.reset(launch.state.pipeline_id)
    tel.LLMMeter.attach_durable_run(launch.state.pipeline_id, str(launch.ledger),
                                    "next-parent", existing_only=True)
    with pytest.raises(UsageLedgerUnresolvedError):
        tel.LLMMeter.assert_accounting_available(launch.state.pipeline_id)


def test_pool_workers_share_parent_and_keep_run_stage_without_contextvars(launch):
    process = _child(launch, "pool")
    assert process.returncode == 0, process.stderr
    assert _last_json(process)["provider_calls"] == 2
    snap = _snapshot(launch)
    assert snap["by_stage"]["run"]["total_tokens"] == 120
    assert snap["fallback_attributed"]["calls"] == 2
    assert snap["api_operation_state"]["in_flight"] == 0


def test_child_completion_permits_sibling_pending_but_parent_completion_rejects_it(launch):
    UsageLedger(str(launch.ledger)).record_snapshot(
        run_id=launch.state.pipeline_id, attempt_id=launch.context["attempt_id"],
        source="llm_api_attempt", operation_id="other-launch:sibling-pending",
        metadata={"stage": "run", "provider": "openai", "model": "fixture-model",
                  "usage_class": "unknown", "billing_basis": "api"},
        counters={"calls": 0}, status="in_flight")
    process = _child(launch, "complete")
    assert process.returncode == 0, process.stderr
    assert _last_json(process) == {"complete": True}
    with pytest.raises(UsageLedgerUnresolvedError):
        tel.LLMMeter.assert_accounting_settled(launch.state.pipeline_id)


def test_child_completion_rejects_its_own_pending_api_observation(launch):
    process = _child(launch, "own_pending")
    assert process.returncode == 19, process.stderr
    assert _last_json(process) == {"error": "UsageLedgerUnresolvedError", "provider_calls": 0}
    assert _snapshot(launch)["api_operation_state"]["in_flight"] == 1
    with pytest.raises(UsageLedgerUnresolvedError):
        tel.LLMMeter.assert_accounting_settled(launch.state.pipeline_id)


def test_completion_is_blocked_while_marker_commit_returns_to_meter(launch, monkeypatch):
    _attach_child_scope(launch)
    operation_id = tel.LLMMeter.new_operation_id(launch.state.pipeline_id)
    committed = threading.Event()
    release = threading.Event()
    original = UsageLedger.record_snapshot

    def delayed_return(ledger, **kwargs):
        result = original(ledger, **kwargs)
        if kwargs["operation_id"] == operation_id:
            committed.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(UsageLedger, "record_snapshot", delayed_return)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(tel.LLMMeter.record_snapshot,
                              "llm_api_attempt", operation_id, "openai", "fixture-model", 0, 0, 0.0,
                              calls=0, status="in_flight", usage_source="unknown",
                              run_id=launch.state.pipeline_id, stage="run")
        try:
            assert committed.wait(10)
            with pytest.raises(UsageLedgerUnresolvedError):
                tel.LLMMeter.assert_local_accounting_settled(launch.state.pipeline_id)
        finally:
            release.set()
            pending.result(timeout=10)


@pytest.mark.parametrize("growth", [False, True])
def test_terminal_snapshot_replay_cannot_poison_local_completion(launch, growth):
    _attach_child_scope(launch)
    operation_id = tel.LLMMeter.new_operation_id(launch.state.pipeline_id)
    arguments = {"run_id": launch.state.pipeline_id, "stage": "run"}
    tel.LLMMeter.record_snapshot("llm_api_attempt", operation_id, "openai", "fixture-model",
                                10, 5, 0.0, usage_source="known", **arguments)
    tel.LLMMeter.record_snapshot("llm_api_attempt", operation_id, "openai", "fixture-model",
                                20 if growth else 0, 5 if growth else 0, 0.0,
                                calls=1 if growth else 0, status="in_flight",
                                usage_source="unknown", **arguments)
    assert _snapshot(launch)["api_operation_state"]["in_flight"] == 0
    tel.LLMMeter.assert_local_accounting_settled(launch.state.pipeline_id)


def _attach_child_scope(launch):
    tel.LLMMeter.reset(launch.state.pipeline_id)
    tel.LLMMeter.attach_durable_run(
        launch.state.pipeline_id, str(launch.ledger), launch.context["attempt_id"],
        existing_only=True, default_stage="run", operation_scope=launch.context["launch_token"])


def test_cli_estimate_is_durable_with_optional_telemetry_disabled(launch):
    process = _child(launch, "cli")
    assert process.returncode == 0, process.stderr
    snap = _snapshot(launch)
    assert snap["total"]["calls"] == 1
    assert snap["total"]["total_tokens"] > 0
    assert snap["cost_basis"] == "subscription"
    assert snap["by_stage"]["run"]["calls"] == 1


def test_child_admission_observes_parent_spend_committed_after_launch(launch, monkeypatch):
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 50)
    launch.context = launch.orch._simulation_usage_context(launch.state, launch.sim_id)
    env = _environment(launch)
    tel.LLMMeter.record("openai", "parent-model", 60, 0, 0.0,
                        run_id=launch.state.pipeline_id, stage="research")
    process = _child(launch, "retry", env=env)
    assert process.returncode == 17
    assert _last_json(process) == {"error": "BudgetExceeded", "provider_calls": 0}
    assert _snapshot(launch)["total"]["total_tokens"] == 60


def test_missing_parent_ledger_is_not_recreated_by_bootstrap(launch):
    env = _environment(launch)
    launch.ledger.unlink()
    process = _child(launch, env=env)
    assert process.returncode == 17
    assert _last_json(process) == {"error": "UsageLedgerStorageError", "provider_calls": 0}
    assert not launch.ledger.exists()


@pytest.mark.parametrize("field", ["run_id", "attempt_id", "simulation_id", "launch_token", "ledger_path"])
def test_child_rejects_wrong_lineage_before_provider_work(launch, field):
    env = _environment(launch)
    context = json.loads(env[su.CONTEXT_ENV])
    context[field] = str(launch.ledger.parent / "wrong.sqlite3") if field == "ledger_path" else "wrong"
    env[su.CONTEXT_ENV] = json.dumps(context)
    process = _child(launch, env=env)
    assert process.returncode == 17
    assert _last_json(process) == {"error": "UsageLedgerConflict", "provider_calls": 0}
    assert _snapshot(launch)["total"]["calls"] == 0


def test_config_changed_after_launch_admission_is_rejected(launch):
    env = _environment(launch)
    launch.config.write_text(json.dumps({"simulation_id": launch.sim_id, "changed": True}))
    process = _child(launch, env=env)
    assert process.returncode == 17
    assert _last_json(process)["provider_calls"] == 0


@pytest.mark.parametrize("raw", ["", "null", "[]", "{malformed"])
def test_present_invalid_context_cannot_silently_enter_legacy_mode(launch, raw):
    env = _environment(launch)
    env[su.CONTEXT_ENV] = raw
    process = _child(launch, env=env)
    assert process.returncode == 17
    assert _last_json(process)["provider_calls"] == 0


def test_unbound_launch_clears_inherited_accounting_context(launch):
    env = su.child_environment({su.CONTEXT_ENV: "stale", "UNRELATED": "preserved"}, None, str(launch.config))
    assert env == {"UNRELATED": "preserved"}
    child_env = _environment(launch)
    child_env.pop(su.CONTEXT_ENV)
    process = _child(launch, env=child_env)
    assert process.returncode == 0
    assert _last_json(process) == {"authority": None}


def test_matching_shared_receipt_is_diagnostic_and_replay_adds_no_usage(launch):
    process = _child(launch, "retry")
    assert process.returncode == 0, process.stderr
    before = _snapshot(launch)["total"]
    for _ in range(3):
        launch.orch._record_durable_sim_usage(launch.state, launch.sim_id, _payload(launch))
    assert _snapshot(launch)["total"] == before
    assert launch.state.options["sim_llm_telemetry"]["diagnostic_only"] is True
    assert launch.state.options["sim_llm_telemetry_recorded"]["authority"] == "shared_usage_ledger"


@pytest.mark.parametrize("change", ["missing_authority", "wrong_authority", "unknown_launch", "wrong_simulation",
                                    "foreign_token_without_authority", "empty_token_without_authority"])
def test_new_authority_cannot_downgrade_or_import_another_launch(launch, change):
    payload = _payload(launch)
    if change == "missing_authority":
        payload.pop("usage_authority")
    elif change == "wrong_authority":
        payload["usage_authority"] = {**payload["usage_authority"], "attempt_id": "other"}
    elif change == "unknown_launch":
        payload["meter_run_token"] = "unadmitted"
    elif change in {"foreign_token_without_authority", "empty_token_without_authority"}:
        payload.pop("usage_authority")
        payload["meter_run_token"] = "foreign" if change.startswith("foreign") else ""
    else:
        payload["simulation_id"] = "other"
    with pytest.raises(UsageLedgerConflict):
        launch.orch._record_durable_sim_usage(launch.state, launch.sim_id, payload)
    assert _snapshot(launch)["total"]["calls"] == 0


def test_genuinely_legacy_snapshot_still_reconciles_once(launch):
    legacy_sim = "sim_legacy_unshared"
    payload = _payload(launch, simulation_id=legacy_sim, meter_run_token="historic-child", prompt_tokens=100,
                       completion_tokens=20, total_tokens=120, calls=2)
    payload.pop("usage_authority")
    for _ in range(3):
        launch.orch._record_durable_sim_usage(launch.state, legacy_sim, payload)
    assert _snapshot(launch)["total"]["total_tokens"] == 120
    assert _snapshot(launch)["total"]["calls"] == 2


def test_parallel_launch_receipts_are_saved_before_dispatch(launch):
    with ThreadPoolExecutor(max_workers=3) as pool:
        contexts = list(pool.map(lambda index: launch.orch._simulation_usage_context(
            launch.state, f"sim-ensemble-{index}"), range(3)))
    saved = json.loads(Path(launch.context["pipeline_state_path"]).read_text())
    receipts = saved["options"]["simulation_usage_launches"]
    assert len(receipts) == 4
    assert all(receipts[context["launch_token"]] == su.authority(context) for context in contexts)
    assert {context["attempt_id"] for context in contexts} == {launch.context["attempt_id"]}
