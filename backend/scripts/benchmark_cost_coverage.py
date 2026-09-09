#!/usr/bin/env python3
"""Compare recorded price-coverage admission and indexed reads, offline.

Only two API dispatch symbols come from local Git. Both modes use the current
ledger, captured quotes, real SDK and native ModelFactory. Mock transports and
socket guards prevent provider access. Read timings exclude fixture writes.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import patch
import uuid

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = "a000e29dfb080ad741da4a23fc2312dd9280b3ef"
MODEL = "gpt-4o-mini"
OUTPUT_LIMIT = 2048
MESSAGES = [{"role": "user", "content": "offline fixture"}]
CONTENT = "accepted"
SCENARIOS = ("prior_unpriced_history", "defaults_disabled", "fresh_priced",
             "explicit_quoted_zero", "same_owner_concurrency")
ROUTES = ("shared_sync", "native_sync", "native_async")


def _response() -> dict[str, Any]:
    return {"id": "offline-response", "object": "chat.completion", "created": 1,
            "model": MODEL, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": CONTENT}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}}


def _load_baseline(revision, runtime):
    symbols, receipts = {}, {}
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
        namespace = {**vars(module), "__name__": "app.utils._astra_baseline_cost_coverage",
                     "capture_cost_quote": runtime.api_cost.capture_cost_quote}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<local-git-coverage-boundary>", "exec"), namespace)
        symbols[label] = namespace[name]
        receipts[label] = {"path": "backend/" + path, "symbol": name,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "symbol_ast_sha256": hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()}
    return symbols, receipts


def _accounting(runtime, ledger: Path, run_id: str) -> dict[str, Any]:
    snapshot = runtime.tel.LLMMeter.cumulative_snapshot(run_id)
    with sqlite3.connect(ledger) as connection:
        operations = connection.execute(
            "SELECT operation_id,metadata_json,counter_json,status FROM usage_operations "
            "WHERE run_id=? AND source='llm_api_attempt' ORDER BY operation_id", (run_id,)).fetchall()
        deltas = connection.execute("SELECT * FROM usage_deltas WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        holds = connection.execute("SELECT * FROM usage_reservations WHERE run_id=? ORDER BY operation_id",
                                   (run_id,)).fetchall()
        policy = connection.execute("SELECT * FROM usage_token_policy WHERE run_id=?", (run_id,)).fetchall()
    # The digest establishes exact operation-history preservation without
    # exporting identities or metadata. Synthetic latency is not compared.
    history = hashlib.sha256(json.dumps(operations, separators=(",", ":")).encode()).hexdigest()
    return {"recorded_calls": snapshot["total"]["calls"],
            "recorded_tokens": snapshot["total"]["total_tokens"],
            "recorded_cost_usd": snapshot["total"]["cost_usd"],
            "operation_rows": len(operations), "delta_rows": len(deltas), "token_hold_rows": len(holds),
            "token_policy_rows": len(policy),
            "operation_history_sha256": history,
            "delta_history_sha256": hashlib.sha256(json.dumps(deltas, separators=(",", ":")).encode()).hexdigest(),
            "token_history_sha256": hashlib.sha256(json.dumps([holds, policy], separators=(",", ":")).encode()).hexdigest(),
            "in_flight_operations": snapshot["api_operation_state"]["in_flight"],
            "unknown_operations": snapshot["api_operation_state"]["unknown"],
            "token_reservation_state": snapshot["token_reservation_state"],
            "cost_coverage": snapshot["cost_coverage"]}


async def _case(runtime, directory: Path, scenario: str, route: str, *, before: bool, baseline):
    run_id = "offline-cost-coverage-" + uuid.uuid4().hex
    ledger = directory / (scenario + "-" + route + "-" + str(before) + ".sqlite3")
    provider = "antigravity" if scenario == "prior_unpriced_history" else "openai"
    cap = 0 if scenario in ("prior_unpriced_history", "defaults_disabled") else 1
    rates = "" if cap == 0 else json.dumps({provider: [0, 0] if scenario == "explicit_quoted_zero" else [1, 2]})
    requests, clients = [], []
    entered, release = threading.Event(), threading.Event()
    entered_async, release_async = asyncio.Event(), asyncio.Event()
    transport_lock = threading.Lock()
    peak, active, count = 0, 0, 0
    previous = runtime.tel.get_run_context()

    def observe(request):
        nonlocal peak, active, count
        with transport_lock:
            count += 1
            sequence = count
            active += 1
            peak = max(peak, active)
        body = json.loads(request.content)
        requests.append({"sequence": sequence, "serialized_fields": sorted(body),
                         "serialized_body_sha256": hashlib.sha256(request.content).hexdigest(),
                         "model_matches_fixture": body.get("model") == MODEL,
                         "messages_match_fixture": body.get("messages") == MESSAGES,
                         "before_response": _accounting(runtime, ledger, run_id)})
        return sequence

    def leave():
        nonlocal active
        with transport_lock:
            active -= 1

    def transport(request):
        sequence = observe(request)
        try:
            if scenario == "same_owner_concurrency" and sequence == 1:
                entered.set()
                if not release.wait(10):
                    raise AssertionError("Fixture did not release its first request")
            return runtime.httpx.Response(200, json=_response())
        finally:
            leave()

    async def async_transport(request):
        sequence = observe(request)
        try:
            if scenario == "same_owner_concurrency" and sequence == 1:
                entered_async.set()
                await asyncio.wait_for(release_async.wait(), timeout=10)
            return runtime.httpx.Response(200, json=_response())
        finally:
            leave()

    def sync_constructor(**kwargs):
        sdk = runtime.OpenAI(**kwargs, http_client=runtime.httpx.Client(
            transport=runtime.httpx.MockTransport(transport), trust_env=False))
        clients.append((False, sdk))
        return sdk

    def async_constructor(**kwargs):
        sdk = runtime.AsyncOpenAI(**kwargs, http_client=runtime.httpx.AsyncClient(
            transport=runtime.httpx.MockTransport(async_transport), trust_env=False))
        clients.append((True, sdk))
        return sdk

    try:
        with ExitStack() as stack:
            for key, value in {"LLM_RUN_BUDGET_USD": cap, "LLM_COST_PER_MTOK": rates,
                               "LLM_RUN_BUDGET_TOKENS": 0 if scenario == "defaults_disabled" else 100000}.items():
                stack.enter_context(patch.object(runtime.Config, key, value))
            stack.enter_context(patch.dict("os.environ", {"LLM_PROVIDER": provider}))
            runtime.tel.LLMMeter.attach_durable_run(run_id, str(ledger), attempt_id="same-parent",
                                                  default_stage="run", operation_scope="fixture")
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

            def invoke_sync():
                old_context = runtime.tel.get_run_context()
                runtime.tel.set_run_context(run_id, "run")
                try:
                    content = (client.chat(MESSAGES, max_tokens=OUTPUT_LIMIT) if route == "shared_sync"
                               else client.run(MESSAGES).choices[0].message.content)
                    return {"outcome": "success", "content_matches_fixture": content == CONTENT}
                except runtime.tel.BudgetExceeded:
                    return {"outcome": "BudgetExceeded", "content_matches_fixture": None}
                finally:
                    runtime.tel.set_run_context(*old_context)

            async def invoke():
                if route != "native_async":
                    return invoke_sync()
                try:
                    content = (await client.arun(MESSAGES)).choices[0].message.content
                    return {"outcome": "success", "content_matches_fixture": content == CONTENT}
                except runtime.tel.BudgetExceeded:
                    return {"outcome": "BudgetExceeded", "content_matches_fixture": None}

            history, history_invocation, overlap = None, None, None
            if scenario == "prior_unpriced_history":
                history_invocation = await invoke()
                history = _accounting(runtime, ledger, run_id)
                runtime.Config.LLM_COST_PER_MTOK = json.dumps({provider: [1, 2]})
                runtime.Config.LLM_RUN_BUDGET_USD = 1
            if scenario == "same_owner_concurrency":
                if route == "native_async":
                    first = asyncio.create_task(invoke())
                    try:
                        await asyncio.wait_for(entered_async.wait(), timeout=10)
                        second_result = await invoke()
                        overlap = _accounting(runtime, ledger, run_id)
                    finally:
                        release_async.set()
                    first_result = await first
                else:
                    first = asyncio.create_task(asyncio.to_thread(invoke_sync))
                    try:
                        assert await asyncio.to_thread(entered.wait, 10), "First fixture request did not arrive"
                        second_result = await asyncio.to_thread(invoke_sync)
                        overlap = _accounting(runtime, ledger, run_id)
                    finally:
                        release.set()
                    first_result = await first
                outcomes = [first_result, second_result]
            else:
                outcomes = [await invoke()]
            final = _accounting(runtime, ledger, run_id)
            result = {"provider_attribution": provider, "route": route,
                      "initial_usd_budget": cap, "final_usd_budget": runtime.Config.LLM_RUN_BUDGET_USD,
                      "initial_configured_rates_usd_per_million": json.loads(rates) if rates else {},
                      "final_configured_rates_usd_per_million": json.loads(runtime.Config.LLM_COST_PER_MTOK)
                          if runtime.Config.LLM_COST_PER_MTOK else {},
                      "token_budget": runtime.Config.LLM_RUN_BUDGET_TOKENS,
                      "initial_history_invocation": history_invocation,
                      "history_before_enabling_usd": history,
                      "invocations": outcomes, "mock_transport_requests": len(requests),
                      "additional_transport_requests": len(requests) - (1 if history is not None else 0),
                      "peak_simultaneous_mock_requests": peak,
                      "while_first_request_held_after_second_settled": overlap,
                      "requests": sorted(requests, key=lambda r: r["sequence"]),
                      "after_response": final,
                      "rejected_request_preserves_exact_history": final == history if history is not None and not before else None}
            runtime.tel.LLMMeter.assert_accounting_available(run_id)
            result["accounting_available_after_case"] = True
            _validate(scenario, result, before)
            return result
    finally:
        release.set()
        release_async.set()
        try:
            for asynchronous, sdk in clients:
                if asynchronous:
                    await sdk.close()
                else:
                    sdk.close()
        finally:
            runtime.tel.LLMMeter.reset(run_id)
            runtime.tel.set_run_context(*previous)


def _validate(scenario, result, before):
    blocked = scenario == "prior_unpriced_history" and not before
    expected = "BudgetExceeded" if blocked else "success"
    assert all(x["outcome"] == expected for x in result["invocations"]), result
    assert all(x["content_matches_fixture"] == (None if blocked else True) for x in result["invocations"]), result
    calls = (1 if blocked else 2) if scenario in ("prior_unpriced_history", "same_owner_concurrency") else 1
    assert result["mock_transport_requests"] == calls, result
    final = result["after_response"]
    assert final["recorded_calls"] == final["operation_rows"] == calls, result
    assert final["recorded_tokens"] == 2000 * calls, result
    assert final["in_flight_operations"] == final["unknown_operations"] == 0, result
    assert final["token_reservation_state"]["active_operations"] == 0, result
    assert final["token_reservation_state"]["reserved_tokens"] == 0, result
    expected_cost = {"prior_unpriced_history": 0 if blocked else 0.003,
                     "defaults_disabled": 0.02, "fresh_priced": 0.003,
                     "explicit_quoted_zero": 0, "same_owner_concurrency": 0.006}[scenario]
    assert Decimal(str(final["recorded_cost_usd"])) == Decimal(str(expected_cost)), result
    for request in result["requests"]:
        assert request["model_matches_fixture"] and request["messages_match_fixture"], result
    coverage = final["cost_coverage"]
    assert coverage["priced_api_operations"] == calls - int(scenario == "prior_unpriced_history"), result
    assert coverage["unpriced_api_operations"] == int(scenario == "prior_unpriced_history"), result
    assert all(coverage[key] == 0 for key in ("unquoted_api_operations", "inexact_api_operations",
                                            "non_api_operations", "cache_only_operations")), result
    assert coverage["price_coverage_complete"] == (scenario != "prior_unpriced_history"), result
    assert coverage["usage_complete"] is False, result
    if scenario == "prior_unpriced_history":
        initial = result["history_before_enabling_usd"]
        assert result["initial_history_invocation"]["outcome"] == "success", result
        assert initial["recorded_calls"] == initial["operation_rows"] == 1, result
        assert initial["recorded_tokens"] == 2000 and initial["recorded_cost_usd"] == 0, result
        assert initial["cost_coverage"]["unpriced_api_operations"] == 1, result
        if blocked:
            assert result["rejected_request_preserves_exact_history"] is True, result
    if scenario == "same_owner_concurrency":
        assert result["peak_simultaneous_mock_requests"] == 2, result
        pending = result["while_first_request_held_after_second_settled"]
        assert pending["in_flight_operations"] == 1 and pending["recorded_calls"] == 1, result
        assert pending["cost_coverage"]["priced_api_operations"] == 2, result
        assert pending["cost_coverage"]["inexact_api_operations"] == 1, result
        assert pending["cost_coverage"]["price_coverage_complete"] is True, result


def _source_hashes():
    paths = ["app/config.py", "app/utils/llm_client.py", "app/utils/oasis_llm.py",
             "app/utils/oasis_usage.py", "app/utils/oasis_output_policy.py", "app/utils/simulation_usage.py",
             "app/utils/api_cost.py", "app/utils/api_budget.py", "app/utils/telemetry.py",
             "app/utils/usage_ledger.py", "scripts/benchmark_cost_coverage.py"]
    return {"backend/" + p: hashlib.sha256((BACKEND / p).read_bytes()).hexdigest() for p in paths}


def _scaling_case(runtime, directory: Path, rows: int, samples: int):
    module = runtime.usage_ledger
    path = directory / f"coverage-scaling-{rows}.sqlite3"
    ledger = module.UsageLedger(str(path))
    run_id = "fixture-scaling"
    ledger.initialize(run_id)
    quote = {"schema": "api-cost-quote/v1", "provider": "openai", "model": MODEL,
             "source": "configured_provider", "rate_key": "openai",
             "input_usd_per_1k": 0.001, "output_usd_per_1k": 0.002}
    metadata = {"stage": "run", "provider": "openai", "model": MODEL,
                "usage_class": "known", "billing_basis": "api", "cost_estimated": True,
                "fallback": False, "cache_partition_known": False, "cost_quote": quote}
    counter = module.empty_counter()
    counter.update(calls=1, prompt_tokens=100, completion_tokens=50, total_tokens=150,
                   cost_usd=runtime.api_cost.quote_cost(quote, 100, 50))
    # Synthetic one-operation/one-delta history is deliberately distinct from
    # saved aggregate call counters. One transaction excludes setup from timing.
    with sqlite3.connect(path) as connection:
        for index in range(rows):
            operation = f"fixture-{index:06}"
            connection.execute("INSERT INTO usage_operations VALUES (?,?,?,?,?,?,?,?)",
                (run_id, "llm_api_attempt", operation, "same-parent", json.dumps(metadata),
                 json.dumps(counter), "completed", 1))
            ledger._insert_delta(connection, run_id, "same-parent", "llm_api_attempt",
                                 operation, metadata, counter)
    expected = ledger.snapshot(run_id)
    assert expected["total"]["calls"] == rows
    assert expected["total"]["total_tokens"] == rows * 150
    assert expected["cost_coverage"]["priced_api_operations"] == rows
    price, inexact = module._COST_PRICE_CLASS_SQL, module._COST_INEXACT_SQL
    index_name = "usage_operations_cost_coverage"
    modes = {
        "projection": module.UsageLedger._cost_coverage_state,
        "price_gap_admission": module.UsageLedger._assert_cost_coverage,
    }

    class QueryMode:
        """Change only SQLite's access hint; execute current production methods."""

        def __init__(self, connection, hint, trace=None):
            self.connection, self.hint, self.trace = connection, hint, trace

        def execute(self, sql, parameters):
            anchor = "FROM usage_operations WHERE"
            assert sql.count(anchor) == 1
            adapted = sql.replace(anchor, "FROM usage_operations " + self.hint + " WHERE")
            if self.trace is not None:
                self.trace.append(adapted)
            return self.connection.execute(adapted, parameters)

    measurements = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        index_row = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                                       (index_name,)).fetchone()
        assert index_row is not None
        for label, method in modes.items():
            hints = {"indexed": "INDEXED BY " + index_name, "not_indexed": "NOT INDEXED",
                     "unforced_current_queries": ""}
            queries, results = {}, {}
            for name, hint in hints.items():
                queries[name] = []
                results[name] = method(QueryMode(connection, hint, queries[name]), run_id)
                assert len(queries[name]) == 2, "Production read algorithm changed; review timing scope"
                assert "='api_validate'" in queries[name][1]
                assert connection.execute(queries[name][1], (run_id,)).fetchall() == []
            assert results["indexed"] == results["not_indexed"] == results["unforced_current_queries"]
            assert results["indexed"] == (expected["cost_coverage"] if label == "projection" else None)
            plans = {name: {kind: [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, (run_id,))]
                           for kind, sql in zip(("primary", "validation_lookup"), sql_pair, strict=True)}
                     for name, sql_pair in queries.items()}
            assert all(any(index_name in line for line in detail) for detail in plans["indexed"].values())
            assert all(any(index_name in line for line in detail) for detail in plans["unforced_current_queries"].values())
            assert all(index_name not in line for detail in plans["not_indexed"].values() for line in detail)
            connections = {name: QueryMode(connection, hints[name]) for name in ("indexed", "not_indexed")}
            timings = {name: [] for name in connections}
            for _ in range(2):
                for selected in connections.values():
                    assert method(selected, run_id) == results["indexed"]
            for sample in range(samples):
                order = list(connections)
                if sample % 2:
                    order.reverse()
                for name in order:
                    start = time.perf_counter_ns()
                    value = method(connections[name], run_id)
                    timings[name].append((time.perf_counter_ns() - start) / 1_000_000)
                    assert value == results["indexed"]
            medians = {name: statistics.median(values) for name, values in timings.items()}
            measurements[label] = {
                "samples_per_mode": samples, "warmups_per_mode": 2,
                "method": "UsageLedger." + method.__name__,
                "result": results["indexed"], "validation_lookup_rows": 0, "query_plans": plans,
                "timed_scope": "Current complete coverage-read/admission method, including both SQL reads and Python aggregation. Identical access-hint adapters in both modes; excludes connection/transaction setup and writes.",
                "sample_method_ns": {name: [round(value * 1_000_000) for value in values]
                                     for name, values in timings.items()},
                "median_method_ms": {name: round(value, 6) if samples > 1 else None for name, value in medians.items()},
                "reduction_percent": round(100 * (medians["not_indexed"] - medians["indexed"])
                                           / medians["not_indexed"], 3) if samples > 1 else None,
                "query_pair_sha256": {name: hashlib.sha256(json.dumps(sql_pair).encode()).hexdigest()
                                      for name, sql_pair in queries.items()},
            }
        assert connection.total_changes == 0
    assert ledger.snapshot(run_id) == expected
    return {"operation_rows": rows, "delta_rows": rows, "all_rows_priced_known_completed": True,
            "all_quote_attribution_is_normal_ascii": True,
            "recorded_calls": expected["total"]["calls"],
            "recorded_tokens": expected["total"]["total_tokens"],
            "recorded_cost_usd": expected["total"]["cost_usd"],
            "snapshot_identical_after_reads": True, "timed_connection_total_changes": 0,
            "index_name": index_name, "index_sql_sha256": hashlib.sha256(index_row[0].encode()).hexdigest(),
            "price_expression_sha256": hashlib.sha256(price.encode()).hexdigest(),
            "inexact_expression_sha256": hashlib.sha256(inexact.encode()).hexdigest(),
            "queries": measurements}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, nargs="*", default=[100, 1000, 10000])
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100 or any(not 1 <= n <= 100000 for n in args.rows):
        parser.error("samples must be 1-100 and row counts 1-100000")
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
            from app.utils import api_cost, llm_client as lc, oasis_llm as oasis, oasis_usage as native
            from app.utils import simulation_usage, telemetry as tel, usage_ledger
            runtime = SimpleNamespace(httpx=httpx, OpenAI=OpenAI, AsyncOpenAI=AsyncOpenAI,
                camel_openai=camel_openai, Config=Config, lc=lc, oasis=oasis, native=native,
                tel=tel, api_cost=api_cost, usage_ledger=usage_ledger)
            for key, value in {"LLM_CACHE_ENABLED": False, "LLM_TIERED_ROUTING": False,
                               "LLM_TELEMETRY_ENABLED": True, "OASIS_MAX_OUTPUT_TOKENS": OUTPUT_LIMIT,
                               "OASIS_OUTPUT_TOKEN_PARAMETER": "max_tokens"}.items():
                stack.enter_context(patch.object(Config, key, value))
            stack.enter_context(patch.object(Config, "reasoning_extra_body", return_value=None))
            stack.enter_context(patch.object(simulation_usage, "current_output_policy", return_value=None))
            stack.enter_context(patch.object(lc, "_CB_STATE", {}))
            baseline, baseline_receipts = _load_baseline(revision, runtime)
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="astra-cost-coverage-")))
            cases = {}
            for scenario in SCENARIOS:
                cases[scenario] = {}
                for route in ROUTES:
                    pair = {side: asyncio.run(_case(runtime, directory, scenario, route, before=before, baseline=baseline))
                            for side, before in (("before", True), ("after", False))}
                    old_wire = [x["serialized_body_sha256"] for x in pair["before"]["requests"]]
                    new_wire = [x["serialized_body_sha256"] for x in pair["after"]["requests"]]
                    assert old_wire[:len(new_wire)] == new_wire
                    cases[scenario][route] = pair
            scaling = [_scaling_case(runtime, directory, n, args.samples) for n in args.rows]
            assert connect.call_count == connect_ex.call_count == 0
    finally:
        logging.disable(disabled)
        sys.path[:] = original_path
    if _source_hashes() != hashes:
        raise RuntimeError("Source changed during comparison; rerun after implementation settles")
    saved_receipt = BACKEND.parent / "docs/research/astra-cost-coverage-run-evidence.json"
    receipt = {"schema": "astra-cost-coverage-comparison/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": revision, "baseline_symbols": baseline_receipts, "current_source_sha256": hashes,
        "saved_evidence": {"path": str(saved_receipt.relative_to(BACKEND.parent)),
                           "sha256": hashlib.sha256(saved_receipt.read_bytes()).hexdigest()},
        "runtime_versions": {name: version(name) for name in ("camel-ai", "openai", "httpx")},
        "python_version": sys.version.split()[0], "sqlite_version": sqlite3.sqlite_version,
        "network_connection_attempts": 0, "provider_requests": 0,
        "method": "Only pinned shared _create_openai_completion and native _Attempt replace dispatch boundaries. Both modes retain current captured prices, SDK, native ModelFactory, meter, ledger and token reservation behavior. Baseline does not require prior price coverage; current does when the captured dollar-enabled request is durably bound. MockTransport is installed before native instrumentation.",
        "fixture_request_policy": {"output_limit": OUTPUT_LIMIT, "output_parameter": "max_tokens",
                                   "token_budget": "0 for defaults_disabled; 100000 otherwise"},
        "fixture_reported_usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        "cases": cases, "indexed_read_scaling": scaling,
        "indexed_read_method": "Execute current _cost_coverage_state and _assert_cost_coverage through symmetric SQL access-hint adapters. Both production queries and Python aggregation run inside each sample. Normal-ASCII fixtures yield zero rare-validation rows; complete states and unforced plans are verified separately.",
        "limits": [
            "Historical unpriced use is one synthetic known-usage API response under USD0, followed by configured rates [1,2] dollars per million and USD1. Avoided transmissions are fixture behavior, not a historical physical-request or billed-savings claim.",
            "Explicit configured zero rates are priced estimates. Built-in missing/zero placeholders remain unpriced; no invoice accuracy, cache discount or complete consumption coverage is claimed.",
            "Same-owner priced requests overlap deliberately. This price-only prerequisite does not serialize or reserve a shared dollar remainder, pin run-wide rates/caps or fence all older writers.",
            "Both sides use the current coverage projection; no claim is made that the baseline implemented that projection. Exact operation-history equality includes no new marker, delta or token hold after rejection.",
            "Defaults refer to disabled monetary/token limits and empty configured prices. Every fixture retains an explicit output cap and disables cache, fallback and routing for isolation.",
            "Indexed timings compare identical current SQL with INDEXED BY versus NOT INDEXED, not a historical coverage implementation. Samples are local read latency with warm caches; writes and provider latency are excluded.",
            "100 synthetic operations approximate small recorded histories; 1000 is sensitivity and 10000 is scaling stress. Saved aggregate call counters do not establish actual operation-row counts.",
            "Scaling uses normal ASCII provider/model attribution and valid priced known terminal operations only; it does not measure malformed or Unicode fallback classification paths.",
            "No provider, CLI subprocess, deployment, original saved-run mutation, production speedup, billed savings or Pareto optimum is claimed.",
        ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "case_invocations": 2 * len(SCENARIOS) * len(ROUTES),
                      "mock_transport_requests": sum(r[side]["mock_transport_requests"] for c in cases.values()
                                                      for r in c.values() for side in ("before", "after")),
                      "network_connection_attempts": 0, "scaling_rows": args.rows}, indent=2))


if __name__ == "__main__":
    main()
