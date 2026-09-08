#!/usr/bin/env python3
"""Compare native output-policy admission through the real CAMEL factory, offline.

Only _create_openai_model is extracted from pinned local Git for the baseline.
Both sides use current create_oasis_model, OpenAI SDKs, HTTP accounting and fresh
temporary ledgers. Constructors receive MockTransport before instrumentation.
The comparison measures configuration behavior, not production latency or cost.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import uuid

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = "b0532c91b6a90f2ce31a467aec82b43cb4d75688"
MODEL = "gpt-4o-mini"
OUTPUT_CAP = 10
RUN_TOKEN_CAP = 1000
FIXTURE_CONTENT = "accepted"
MESSAGES = [{"role": "user", "content": "offline fixture"}]
OUTPUT_PARAMETERS = ("max_tokens", "max_completion_tokens")


def _response() -> dict[str, Any]:
    return {
        "id": "offline-response", "object": "chat.completion", "created": 1,
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": FIXTURE_CONTENT}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _load_baseline(revision: str, module):
    source = subprocess.check_output(
        ["git", "show", revision + ":backend/app/utils/oasis_llm.py"],
        cwd=BACKEND, text=True,
    )
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_create_openai_model")
    namespace = {**vars(module), "__name__": "app.utils._astra_baseline_native_output"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<local-git-native-factory>", "exec"), namespace)
    return namespace[function.name], hashlib.sha256(source.encode()).hexdigest(), hashlib.sha256(
        ast.dump(function, include_attributes=False).encode()).hexdigest()


def _accounting(tel, run_id: str) -> dict[str, Any]:
    snapshot = tel.LLMMeter.cumulative_snapshot(run_id)
    return {
        "recorded_calls": snapshot["total"]["calls"],
        "recorded_tokens": snapshot["total"]["total_tokens"],
        "unknown_operations": snapshot["api_operation_state"]["unknown"],
        "in_flight_operations": snapshot["api_operation_state"]["in_flight"],
        "token_reservation_state": snapshot["token_reservation_state"],
    }


def _request_receipt(request, accounting: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(request.content)
    # Independently reproduce only the documented numeric input estimate; do
    # not call the planner being evaluated to establish the expected hold.
    inputs = {key: body[key] for key in (
        "messages", "tools", "functions", "response_format", "tool_choice", "function_call")
        if body.get(key) is not None}
    input_bytes = len(json.dumps(inputs, ensure_ascii=False, separators=(",", ":"),
                                allow_nan=False).encode("utf-8"))
    return {
        "serialized_fields": sorted(body),
        "serialized_body_sha256": hashlib.sha256(request.content).hexdigest(),
        "model_matches_fixture": body.get("model") == MODEL,
        "messages_match_fixture": body.get("messages") == MESSAGES,
        "output_parameters": {key: {"present": key in body, "value": body.get(key)}
                              for key in OUTPUT_PARAMETERS},
        "input_json_bytes": input_bytes,
        "estimated_input_tokens": (input_bytes + 3) // 4,
        "before_response_settlement": accounting,
    }


async def _exercise(runtime, directory: Path, label: str, *, output_cap: int,
                    parameter: str, token_cap: int, asynchronous: bool, baseline=None) -> dict[str, Any]:
    run_id = "offline-native-output-" + uuid.uuid4().hex
    previous_context = runtime.tel.get_run_context()
    requests = []
    clients = []
    model = None

    def transport(request):
        requests.append(_request_receipt(request, _accounting(runtime.tel, run_id)))
        return runtime.httpx.Response(200, json=_response())

    def sync_constructor(**kwargs):
        assert kwargs["api_key"] == "offline-fixture"
        assert str(kwargs["base_url"]) == "https://offline.invalid/v1"
        client = runtime.OpenAI(**kwargs, http_client=runtime.httpx.Client(
            transport=runtime.httpx.MockTransport(transport), trust_env=False))
        clients.append((False, client))
        return client

    def async_constructor(**kwargs):
        assert kwargs["api_key"] == "offline-fixture"
        assert str(kwargs["base_url"]) == "https://offline.invalid/v1"
        client = runtime.AsyncOpenAI(**kwargs, http_client=runtime.httpx.AsyncClient(
            transport=runtime.httpx.MockTransport(transport), trust_env=False))
        clients.append((True, client))
        return client

    try:
        with ExitStack() as stack:
            for key, value in {"OASIS_MAX_OUTPUT_TOKENS": output_cap,
                               "OASIS_OUTPUT_TOKEN_PARAMETER": parameter,
                               "LLM_RUN_BUDGET_TOKENS": token_cap}.items():
                stack.enter_context(patch.object(runtime.Config, key, value))
            stack.enter_context(patch.object(runtime.camel_openai, "OpenAI", sync_constructor))
            stack.enter_context(patch.object(runtime.camel_openai, "AsyncOpenAI", async_constructor))
            stack.enter_context(patch.object(runtime.camel_openai, "is_langfuse_available", lambda: False))
            runtime.tel.LLMMeter.attach_durable_run(
                run_id, str(directory / (label + ".sqlite3")), attempt_id="fixture-parent",
                default_stage="run", operation_scope="fixture")
            runtime.tel.set_run_context(run_id, "run")
            with ExitStack() as factory_stack:
                if baseline is not None:
                    factory_stack.enter_context(patch.object(runtime.oasis, "_create_openai_model", baseline))
                model = runtime.oasis.create_oasis_model({"llm_provider": "openai", "llm_model": MODEL})
            assert isinstance(model, runtime.camel_openai.OpenAIModel)
            assert len(clients) == 2
            initial_config = dict(model.model_config_dict)
            context_limit = model.token_limit
            try:
                response = await model.arun(MESSAGES) if asynchronous else model.run(MESSAGES)
                outcome = "success"
                content_matches = response.choices[0].message.content == FIXTURE_CONTENT
                usage_matches = response.usage.model_dump(exclude_none=True) == _response()["usage"]
            except runtime.tel.BudgetExceeded:
                outcome = "BudgetExceeded"
                content_matches = usage_matches = None
            result = {
                "invocation": "arun" if asynchronous else "run",
                "configured_output_cap": output_cap,
                "configured_output_parameter": parameter,
                "configured_run_token_cap": token_cap,
                "factory_model_class": type(model).__name__,
                "sdk_classes": [type(client).__name__ for _, client in clients],
                "model_context_limit": context_limit,
                "model_config_unchanged_by_request": model.model_config_dict == initial_config,
                "model_config_output_parameters": {key: {"present": key in initial_config,
                                                         "value": initial_config.get(key)}
                                                   for key in OUTPUT_PARAMETERS},
                "outcome": outcome, "content_matches_fixture": content_matches,
                "response_usage_matches_fixture": usage_matches,
                "mock_transport_requests": len(requests), "requests": requests,
                "final_accounting": _accounting(runtime.tel, run_id),
            }
            _validate_case(result, baseline is not None)
            return result
    finally:
        try:
            for asynchronous_client, client in clients:
                if asynchronous_client:
                    await client.close()
                else:
                    client.close()
        finally:
            runtime.tel.LLMMeter.reset(run_id)
            runtime.tel.set_run_context(*previous_context)


def _validate_case(result: dict[str, Any], baseline: bool) -> None:
    expected_admitted = result["configured_run_token_cap"] == 0 or (
        result["configured_output_cap"] > 0 and not baseline)
    assert result["mock_transport_requests"] == int(expected_admitted), result
    assert result["outcome"] == ("success" if expected_admitted else "BudgetExceeded"), result
    assert result["model_config_unchanged_by_request"], result
    final = result["final_accounting"]
    assert final["recorded_calls"] == int(expected_admitted), result
    assert final["recorded_tokens"] == 15 * int(expected_admitted), result
    assert final["in_flight_operations"] == final["unknown_operations"] == 0, result
    assert final["token_reservation_state"]["reserved_tokens"] == 0, result
    if not expected_admitted:
        assert result["content_matches_fixture"] is result["response_usage_matches_fixture"] is None, result
        return
    assert result["content_matches_fixture"] and result["response_usage_matches_fixture"], result
    request = result["requests"][0]
    assert request["model_matches_fixture"] and request["messages_match_fixture"], result
    active_cap = result["configured_output_cap"] if not baseline else 0
    for key, observed in request["output_parameters"].items():
        expected = active_cap if key == result["configured_output_parameter"] and active_cap else None
        assert observed["value"] == expected, result
        assert observed["present"] == (expected is not None), result
    pending = request["before_response_settlement"]
    assert pending["recorded_calls"] == pending["recorded_tokens"] == 0, result
    assert pending["in_flight_operations"] == 1, result
    hold = pending["token_reservation_state"]
    expected_hold = (request["estimated_input_tokens"] + active_cap
                     if result["configured_run_token_cap"] else 0)
    assert hold["reserved_tokens"] == expected_hold, result
    assert hold["active_operations"] == int(expected_hold > 0), result


def _source_hashes() -> dict[str, str]:
    paths = ["app/config.py", "app/utils/oasis_llm.py", "app/utils/oasis_output_policy.py",
             "app/utils/oasis_usage.py", "app/utils/api_budget.py", "app/utils/telemetry.py",
             "app/utils/usage_ledger.py", "app/utils/simulation_usage.py",
             "scripts/benchmark_native_output_policy.py"]
    return {"backend/" + path: hashlib.sha256((BACKEND / path).read_bytes()).hexdigest()
            for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
        cwd=BACKEND, text=True).strip()
    hashes = _source_hashes()
    disabled = logging.root.manager.disable
    original_sys_path = list(sys.path)
    try:
        logging.disable(logging.CRITICAL)
        with ExitStack() as stack:
            # Offline transports are primary isolation. Socket guards also fail
            # closed if a dependency unexpectedly tries to bypass those clients.
            connect = stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network disabled")))
            connect_ex = stack.enter_context(patch("socket.socket.connect_ex", side_effect=AssertionError("Network disabled")))
            stack.enter_context(patch.dict("os.environ", {
                "LLM_PROVIDER": "openai", "LLM_API_KEY": "offline-fixture",
                "LLM_BASE_URL": "https://offline.invalid/v1", "LLM_MODEL_NAME": MODEL,
                "LLM_BOOST_API_KEY": "", "LLM_FALLBACK_PROVIDER": "", "SIM_LLM_FALLBACK": "false",
                "OPENAI_API_KEY": "offline-fixture", "OPENAI_ORG_ID": "", "OPENAI_PROJECT_ID": "",
                "CAMEL_MODEL_LOG_ENABLED": "false", "LANGFUSE_ENABLED": "false", "TRACEROOT_ENABLED": "false",
            }))
            sys.path.insert(0, str(BACKEND))
            import httpx
            from openai import AsyncOpenAI, OpenAI
            from camel.models import openai_model as camel_openai
            from app.config import Config
            from app.utils import oasis_llm as oasis, simulation_usage, telemetry as tel
            runtime = SimpleNamespace(httpx=httpx, OpenAI=OpenAI, AsyncOpenAI=AsyncOpenAI,
                                      camel_openai=camel_openai, Config=Config, oasis=oasis, tel=tel)
            for key, value in {"LLM_CACHE_ENABLED": False, "LLM_TIERED_ROUTING": False,
                               "LLM_TELEMETRY_ENABLED": True, "LLM_RUN_BUDGET_USD": 0}.items():
                stack.enter_context(patch.object(Config, key, value))
            stack.enter_context(patch.object(Config, "reasoning_extra_body", return_value=None))
            # This harness deliberately exercises the unbound factory's Config
            # path; child launch binding is checked by separate process tests.
            stack.enter_context(patch.object(simulation_usage, "current_output_policy", return_value=None))
            baseline, baseline_hash, function_hash = _load_baseline(revision, oasis)
            temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="astra-native-output-comparison-"))
            cases = {}
            configurations = [("enabled_" + parameter, OUTPUT_CAP, parameter, RUN_TOKEN_CAP)
                              for parameter in OUTPUT_PARAMETERS]
            configurations += [("defaults_disabled", 0, "max_tokens", 0),
                               ("uncapped_token_budget_enabled", 0, "max_tokens", RUN_TOKEN_CAP)]
            for name, output_cap, parameter, token_cap in configurations:
                cases[name] = {}
                for asynchronous in (False, True):
                    mode = "asynchronous" if asynchronous else "synchronous"
                    pair = {}
                    for side, factory in (("before", baseline), ("after", None)):
                        pair[side] = asyncio.run(_exercise(
                            runtime, Path(temporary), name + "-" + mode + "-" + side,
                            output_cap=output_cap, parameter=parameter, token_cap=token_cap,
                            asynchronous=asynchronous, baseline=factory))
                    assert pair["before"]["model_context_limit"] == pair["after"]["model_context_limit"]
                    assert pair["before"]["model_config_output_parameters"] == pair["after"]["model_config_output_parameters"]
                    if name == "defaults_disabled":
                        assert pair["before"]["requests"][0]["serialized_body_sha256"] == pair["after"]["requests"][0]["serialized_body_sha256"]
                    cases[name][mode] = pair
            assert connect.call_count == connect_ex.call_count == 0
    finally:
        logging.disable(disabled)
        sys.path[:] = original_sys_path
    if _source_hashes() != hashes:
        raise RuntimeError("Source changed during comparison; rerun after implementation settles")
    receipt = {
        "schema": "astra-native-cap-comparison/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": revision, "baseline_oasis_llm_sha256": baseline_hash,
        "baseline_factory_function_ast_sha256": function_hash,
        "current_source_sha256": hashes,
        "runtime_versions": {name: version(name) for name in ("camel-ai", "openai", "httpx")},
        "network_connection_attempts": 0, "provider_requests": 0,
        "factory_invocations": 16,
        "method": "Only pinned _create_openai_model replaces the baseline factory helper. Both sides call current create_oasis_model and real CAMEL ModelFactory/OpenAIModel, current SDKs, native accounting and temporary durable ledgers. OpenAI constructors receive MockTransport before any instrumentation or send. No model_config_dict override supplies an output cap.",
        "measurement": "Deterministic behavior, serialized synthetic request fields, pre-response reservations and known usage settlement; no timing measurement.",
        "cases": cases,
        "limits": [
            "The output cap is explicitly enabled for the two admitted-after cases; it remains disabled by default.",
            "The baseline still uses current reservation enforcement and ledger schema. Its uncapped rejection is not a replay of historical production behavior.",
            "Known fixture usage is 10 input plus 5 output tokens. Reservation input uses the labeled UTF-8 JSON estimate, not provider tokenization or an invoice guarantee.",
            "The same synthetic response is returned for both output parameter names. Provider/model support for either parameter is not validated against a live service.",
            "run/arun use real OpenAI SDK construction and chat completion serialization. Child launch binding, ChatAgent memory, boost/Kimi client replacement, retries, structured output and restart behavior require separate regression checks.",
            "No latency improvement, historical budget overshoot, production acceptance or billed savings is claimed. No CLI process, provider, deployment or saved-run mutation occurs.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "factory_invocations": 16,
                      "network_connection_attempts": 0, "cases": list(cases)}, indent=2))


if __name__ == "__main__":
    main()
