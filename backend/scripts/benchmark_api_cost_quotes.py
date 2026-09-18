#!/usr/bin/env python3
"""Compare request price capture with pinned dispatch boundaries, offline.

The baseline loads only LLMClient._create_openai_completion and native _Attempt
from local Git. Both sides use the current SDK, meter and temporary ledger.
MockTransport is installed before native instrumentation; no timing is measured.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import patch
import uuid

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = "186af1a6bcd5ec8587365b398dfeb49d98d2ef63"
MODEL = "gpt-4o-mini"
OUTPUT_LIMIT = 2048
MESSAGES = [{"role": "user", "content": "offline fixture"}]
CONTENT = "accepted"


def _response() -> dict[str, Any]:
    return {"id": "offline-response", "object": "chat.completion", "created": 1,
            "model": MODEL, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": CONTENT}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}}


def _load_baseline(revision: str, runtime):
    result, receipts = {}, {}
    for label, path, module, name in (
        ("shared", "app/utils/llm_client.py", runtime.lc, "_create_openai_completion"),
        ("native", "app/utils/oasis_usage.py", runtime.native, "_Attempt"),
    ):
        source = subprocess.check_output(["git", "show", revision + ":backend/" + path],
                                         cwd=BACKEND, text=True)
        nodes = ast.parse(source).body
        if label == "shared":
            nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == "LLMClient").body
        node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
        namespace = {**vars(module), "__name__": "app.utils._astra_baseline_api_cost"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<local-git-price-boundary>", "exec"), namespace)
        result[label] = namespace[name]
        receipts[label] = {"path": "backend/" + path, "symbol": name,
                           "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                           "symbol_ast_sha256": hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()}
    return result, receipts


def _operation_rows(ledger: Path, run_id: str):
    with sqlite3.connect(ledger) as connection:
        return connection.execute(
            "SELECT operation_id,metadata_json,counter_json,status FROM usage_operations "
            "WHERE run_id=? AND source='llm_api_attempt'", (run_id,)).fetchall()


def _accounting(runtime, ledger: Path, run_id: str) -> dict[str, Any]:
    snapshot = runtime.tel.LLMMeter.cumulative_snapshot(run_id)
    rows = _operation_rows(ledger, run_id)
    return {"recorded_calls": snapshot["total"]["calls"],
            "recorded_tokens": snapshot["total"]["total_tokens"],
            "recorded_cost_usd": snapshot["total"]["cost_usd"],
            "cost_estimated": snapshot["cost_estimated"],
            "operation_rows": len(rows),
            "unknown_operations": snapshot["api_operation_state"]["unknown"],
            "in_flight_operations": snapshot["api_operation_state"]["in_flight"],
            "stored_cost_quotes": [{"present": "cost_quote" in (meta := json.loads(row[1])),
                                     "value": meta.get("cost_quote")} for row in rows]}


async def _case(runtime, directory: Path, name: str, route: str, *, before: bool, baseline):
    run_id = "offline-api-price-" + uuid.uuid4().hex
    ledger = directory / (name + "-" + route + "-" + str(before) + ".sqlite3")
    provider = "antigravity" if name == "missing_price" else "openai"
    cap = 0 if name == "defaults_disabled" else 1
    initial_rates = json.dumps({provider: [1, 2]}) if name == "rate_drift_and_exact_replay" else ""
    requests, clients, terminal_records = [], [], []
    previous = runtime.tel.get_run_context()
    original_record = runtime.tel.LLMMeter.record_snapshot

    def record(*args, **kwargs):
        value = original_record(*args, **kwargs)
        if args[0] == "llm_api_attempt" and kwargs.get("status", "completed") == "completed":
            terminal_records.append((deepcopy(args), deepcopy(kwargs)))
        return value

    def transport(request):
        body = json.loads(request.content)
        requests.append({"serialized_fields": sorted(body),
                         "serialized_body_sha256": hashlib.sha256(request.content).hexdigest(),
                         "model_matches_fixture": body.get("model") == MODEL,
                         "messages_match_fixture": body.get("messages") == MESSAGES,
                         "before_response": _accounting(runtime, ledger, run_id)})
        if name == "rate_drift_and_exact_replay":
            runtime.Config.LLM_COST_PER_MTOK = json.dumps({provider: [2, 4]})
        return runtime.httpx.Response(200, json=_response())

    def sync_constructor(**kwargs):
        client = runtime.OpenAI(**kwargs, http_client=runtime.httpx.Client(
            transport=runtime.httpx.MockTransport(transport), trust_env=False))
        clients.append((False, client))
        return client

    def async_constructor(**kwargs):
        client = runtime.AsyncOpenAI(**kwargs, http_client=runtime.httpx.AsyncClient(
            transport=runtime.httpx.MockTransport(transport), trust_env=False))
        clients.append((True, client))
        return client

    try:
        with ExitStack() as stack:
            for key, value in {"LLM_RUN_BUDGET_USD": cap, "LLM_COST_PER_MTOK": initial_rates}.items():
                stack.enter_context(patch.object(runtime.Config, key, value))
            stack.enter_context(patch.dict("os.environ", {"LLM_PROVIDER": provider}))
            stack.enter_context(patch.object(runtime.tel.LLMMeter, "record_snapshot", staticmethod(record)))
            runtime.tel.LLMMeter.attach_durable_run(
                run_id, str(ledger), attempt_id="fixture-parent", default_stage="run", operation_scope="fixture")
            runtime.tel.set_run_context(run_id, "run")
            if route == "shared_sync":
                sdk = sync_constructor(api_key="offline-fixture", base_url="https://offline.invalid/v1", max_retries=0)
                stack.enter_context(patch.object(runtime.lc.LLMClient, "_build_openai_client", staticmethod(lambda *args: sdk)))
                client = runtime.lc.LLMClient(provider=provider, model=MODEL,
                                              api_key="offline-fixture", base_url="https://offline.invalid/v1")
                if before:
                    client._create_openai_completion = MethodType(baseline["shared"], client)
            else:
                stack.enter_context(patch.object(runtime.camel_openai, "OpenAI", sync_constructor))
                stack.enter_context(patch.object(runtime.camel_openai, "AsyncOpenAI", async_constructor))
                stack.enter_context(patch.object(runtime.camel_openai, "is_langfuse_available", lambda: False))
                if before:
                    stack.enter_context(patch.object(runtime.native, "_Attempt", baseline["native"]))
                client = runtime.oasis.create_oasis_model({"llm_provider": provider, "llm_model": MODEL})
                assert isinstance(client, runtime.camel_openai.OpenAIModel)
            try:
                if route == "shared_sync":
                    content = client.chat(MESSAGES, max_tokens=OUTPUT_LIMIT)
                else:
                    response = await client.arun(MESSAGES) if route == "native_async" else client.run(MESSAGES)
                    content = response.choices[0].message.content
                outcome, matches = "success", content == CONTENT
            except runtime.tel.BudgetExceeded:
                outcome, matches = "BudgetExceeded", None
            settled = _accounting(runtime, ledger, run_id)
            replay = None
            if name == "rate_drift_and_exact_replay":
                assert len(terminal_records) == 1
                rows = _operation_rows(ledger, run_id)
                assert len(rows) == 1 and rows[0][3] == "completed"
                args, kwargs = terminal_records[0]
                assert args[1] == rows[0][0]
                metadata = json.loads(rows[0][1])
                if not before:
                    assert metadata["cost_quote"] == kwargs["cost_quote"]
                    kwargs["cost_quote"] = deepcopy(metadata["cost_quote"])
                else:
                    assert "cost_quote" not in metadata and "cost_quote" not in kwargs
                runtime.Config.LLM_COST_PER_MTOK = json.dumps({provider: [3, 6]})
                original_record(*args, **kwargs)
                replayed = _accounting(runtime, ledger, run_id)
                replay = {"same_operation_replayed": True, "quote_reloaded_from_ledger": not before,
                          "rates_usd_per_million_at_replay": [3, 6],
                          "added_cost_usd": float(Decimal(str(replayed["recorded_cost_usd"]))
                                                  - Decimal(str(settled["recorded_cost_usd"]))),
                          "added_tokens": replayed["recorded_tokens"] - settled["recorded_tokens"],
                          "added_calls": replayed["recorded_calls"] - settled["recorded_calls"],
                          "after_replay": replayed}
            result = {"provider_attribution": provider, "route": route, "usd_budget": cap,
                      "initial_configured_rates_usd_per_million": [1, 2] if initial_rates else None,
                      "rates_during_response_usd_per_million": [2, 4] if initial_rates else None,
                      "outcome": outcome, "content_matches_fixture": matches,
                      "mock_transport_requests": len(requests), "requests": requests,
                      "after_response": settled, "exact_replay": replay}
            _validate(name, result, before)
            return result
    finally:
        try:
            for asynchronous, client in clients:
                if asynchronous:
                    await client.close()
                else:
                    client.close()
        finally:
            runtime.tel.LLMMeter.reset(run_id)
            runtime.tel.set_run_context(*previous)


def _validate(name, result, before):
    admitted = before or name != "missing_price"
    assert result["outcome"] == ("success" if admitted else "BudgetExceeded"), result
    assert result["mock_transport_requests"] == int(admitted), result
    assert result["content_matches_fixture"] == (True if admitted else None), result
    settled = result["after_response"]
    assert settled["recorded_calls"] == settled["operation_rows"] == int(admitted), result
    assert settled["recorded_tokens"] == 2000 * int(admitted), result
    assert settled["unknown_operations"] == settled["in_flight_operations"] == 0, result
    expected_cost = 0 if name == "missing_price" else 0.02 if name == "defaults_disabled" else 0.006 if before else 0.003
    assert settled["recorded_cost_usd"] == expected_cost, result
    if admitted:
        request = result["requests"][0]
        assert request["model_matches_fixture"] and request["messages_match_fixture"], result
        pending = request["before_response"]
        assert pending["recorded_calls"] == pending["recorded_tokens"] == pending["recorded_cost_usd"] == 0, result
        assert pending["in_flight_operations"] == 1, result
        assert pending["stored_cost_quotes"][0]["present"] == (not before), result
        if not before:
            quote = pending["stored_cost_quotes"][0]["value"]
            expected_rates = (0.001, 0.002) if name == "rate_drift_and_exact_replay" else (0.005, 0.015)
            assert quote == {"schema": "api-cost-quote/v1", "provider": "openai", "model": MODEL,
                             "source": "configured_provider" if name == "rate_drift_and_exact_replay" else "builtin_provider",
                             "rate_key": "openai", "input_usd_per_1k": expected_rates[0],
                             "output_usd_per_1k": expected_rates[1]}, result
            assert settled["stored_cost_quotes"][0]["value"] == quote, result
    if name == "rate_drift_and_exact_replay":
        replay = result["exact_replay"]
        assert replay["added_cost_usd"] == (0.003 if before else 0), result
        assert replay["added_calls"] == replay["added_tokens"] == 0, result
        assert replay["after_replay"]["operation_rows"] == 1, result


def _source_hashes():
    paths = ["app/config.py", "app/utils/llm_client.py", "app/utils/oasis_llm.py",
             "app/utils/oasis_usage.py", "app/utils/oasis_output_policy.py", "app/utils/simulation_usage.py",
             "app/utils/api_cost.py", "app/utils/api_budget.py", "app/utils/telemetry.py",
             "app/utils/usage_ledger.py", "scripts/benchmark_api_cost_quotes.py"]
    return {"backend/" + p: hashlib.sha256((BACKEND / p).read_bytes()).hexdigest() for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
                                       cwd=BACKEND, text=True).strip()
    hashes = _source_hashes()
    disabled, original_path = logging.root.manager.disable, list(sys.path)
    try:
        logging.disable(logging.CRITICAL)
        with ExitStack() as stack:
            connect = stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network disabled")))
            connect_ex = stack.enter_context(patch("socket.socket.connect_ex", side_effect=AssertionError("Network disabled")))
            stack.enter_context(patch.dict("os.environ", {
                "LLM_API_KEY": "offline-fixture", "LLM_BASE_URL": "https://offline.invalid/v1",
                "LLM_MODEL_NAME": MODEL, "LLM_BOOST_API_KEY": "", "LLM_FALLBACK_PROVIDER": "",
                "SIM_LLM_FALLBACK": "false", "OPENAI_API_KEY": "offline-fixture",
                "OPENAI_ORG_ID": "", "OPENAI_PROJECT_ID": "", "CAMEL_MODEL_LOG_ENABLED": "false",
                "LANGFUSE_ENABLED": "false", "TRACEROOT_ENABLED": "false",
            }))
            sys.path.insert(0, str(BACKEND))
            import httpx
            from openai import AsyncOpenAI, OpenAI
            from camel.models import openai_model as camel_openai
            from app.config import Config
            from app.utils import llm_client as lc, oasis_llm as oasis, oasis_usage as native, simulation_usage, telemetry as tel
            runtime = SimpleNamespace(httpx=httpx, OpenAI=OpenAI, AsyncOpenAI=AsyncOpenAI,
                camel_openai=camel_openai, Config=Config, lc=lc, oasis=oasis, native=native, tel=tel)
            for key, value in {"LLM_CACHE_ENABLED": False, "LLM_TIERED_ROUTING": False,
                               "LLM_TELEMETRY_ENABLED": True, "LLM_RUN_BUDGET_TOKENS": 0,
                               "OASIS_MAX_OUTPUT_TOKENS": OUTPUT_LIMIT,
                               "OASIS_OUTPUT_TOKEN_PARAMETER": "max_tokens"}.items():
                stack.enter_context(patch.object(Config, key, value))
            stack.enter_context(patch.object(Config, "reasoning_extra_body", return_value=None))
            stack.enter_context(patch.object(simulation_usage, "current_output_policy", return_value=None))
            stack.enter_context(patch.object(lc, "_CB_STATE", {}))
            baseline, baseline_receipts = _load_baseline(revision, runtime)
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="astra-api-cost-comparison-")))
            cases = {}
            for name in ("missing_price", "rate_drift_and_exact_replay", "defaults_disabled"):
                cases[name] = {}
                for route in ("shared_sync", "native_sync", "native_async"):
                    pair = {side: asyncio.run(_case(runtime, directory, name, route, before=before, baseline=baseline))
                            for side, before in (("before", True), ("after", False))}
                    if name != "missing_price":
                        assert pair["before"]["requests"][0]["serialized_body_sha256"] == pair["after"]["requests"][0]["serialized_body_sha256"]
                    cases[name][route] = pair
            assert connect.call_count == connect_ex.call_count == 0
    finally:
        logging.disable(disabled)
        sys.path[:] = original_path
    if _source_hashes() != hashes:
        raise RuntimeError("Source changed during comparison; rerun after implementation settles")
    receipt = {"schema": "astra-api-cost-comparison/v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": revision, "baseline_symbols": baseline_receipts, "current_source_sha256": hashes,
        "runtime_versions": {name: version(name) for name in ("camel-ai", "openai", "httpx")},
        "network_connection_attempts": 0, "provider_requests": 0,
        "method": "Only pinned shared _create_openai_completion and native _Attempt replace baseline dispatch boundaries. Current real SDK clients, native ModelFactory, meter and ledger are shared. MockTransport is installed before instrumentation. Exact replay repeats the terminal observation using the current quote read back from temporary ledger metadata.",
        "measurement": "Deterministic admission, wire equality, quoted settlement and exact replay. No timing measurement.",
        "fixture_request_policy": {"output_limit": OUTPUT_LIMIT, "output_parameter": "max_tokens",
                                   "run_token_budget": 0},
        "fixture_reported_usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        "cases": cases,
        "limits": [
            "The missing-price case uses supported antigravity attribution with a built-in zero-rate placeholder, not a free provider or an observed historical price failure.",
            "The response drift changes configured rates from [1,2] to [2,4] dollars per million tokens: baseline settlement is 0.006 USD and captured settlement is 0.003 USD. A later change to [3,6] adds 0.003 USD on baseline replay, but zero on quoted replay; these are different accounting steps.",
            "Quotes are price estimates over synthetic inclusive usage. They do not prove invoice accuracy, provider tokenization, cache discounts, or physical request savings on historical runs.",
            "Both modes use current ledger storage; baseline omits quotes while current stores them. The defaults case disables dollar budgeting with empty configured rates, preserving the existing rough OpenAI cost and identical requests. All cases retain the explicit fixture output limit.",
            "Token budgets are disabled and dollar-enabled quotes do not reserve money. Monetary concurrency ceilings and unknown-cost history need separate policy and acceptance.",
            "The harness covers one successful response per admitted request, plus accounting replay. SDK retries, malformed prices, restarts and quote tampering require separate regressions.",
            "No provider, CLI subprocess, deployment, original saved-run mutation, latency gain or billed savings is claimed.",
        ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "case_invocations": 18,
                      "network_connection_attempts": 0, "cases": list(cases)}, indent=2))


if __name__ == "__main__":
    main()
