#!/usr/bin/env python3
"""Compare API allowance admission with a pinned physical-call method, offline.

Only LLMClient._create_openai_completion is loaded from local Git. Both methods
use the current outer retry loop, SDK, meter, ledger schema and projections.
Every SDK request uses httpx.MockTransport and a temporary ledger. No saved run
or provider is contacted. Latency samples include durable writes, not setup.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
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
import types
from typing import Any
from unittest.mock import patch
import uuid

import httpx
from openai import OpenAI

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.config import Config  # noqa: E402
from app.utils import llm_client as lc, telemetry as tel, usage_ledger  # noqa: E402

WARMUP_REQUESTS = 3
FIXTURE_CONTENT = "accepted"
REQUEST_OUTPUT_LIMIT = 10


def _response() -> dict[str, Any]:
    return {
        "id": "offline-response", "object": "chat.completion", "created": 1,
        "model": "offline-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": FIXTURE_CONTENT}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _load_baseline(revision: str):
    source = subprocess.check_output(
        ["git", "show", revision + ":backend/app/utils/llm_client.py"],
        cwd=BACKEND, text=True,
    )
    client_class = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.ClassDef) and node.name == "LLMClient")
    method = next(node for node in client_class.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_create_openai_completion")
    namespace = {**vars(lc), "__name__": "app.utils._astra_baseline_reservations"}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<local-git-api-method>", "exec"), namespace)
    return namespace[method.name], hashlib.sha256(source.encode()).hexdigest(), hashlib.sha256(
        ast.dump(method, include_attributes=False).encode()).hexdigest()


@contextmanager
def _run(directory: Path, label: str, token_cap: int, handler, baseline=None):
    run_id = "offline-reservation-" + uuid.uuid4().hex
    ledger_path = directory / (label + ".sqlite3")
    previous = tel.get_run_context()
    sdk = None
    try:
        with patch.object(Config, "LLM_RUN_BUDGET_TOKENS", token_cap):
            tel.LLMMeter.attach_durable_run(
                run_id, str(ledger_path), attempt_id="same-parent",
                default_stage="run", operation_scope="fixture",
            )
            tel.set_run_context(run_id, "run")
            sdk = OpenAI(
                api_key="offline-fixture", base_url="https://offline.invalid/v1",
                max_retries=2,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            with patch.object(lc.LLMClient, "_build_openai_client", staticmethod(lambda *args: sdk)):
                client = lc.LLMClient(provider="openai", model="offline-model", api_key="offline-fixture")
            if baseline is not None:
                client._create_openai_completion = types.MethodType(baseline, client)
            yield types.SimpleNamespace(client=client, run_id=run_id, ledger_path=ledger_path)
    finally:
        try:
            if sdk is not None:
                sdk.close()
        finally:
            tel.LLMMeter.reset(run_id)
            tel.set_run_context(*previous)


def _invoke(run) -> dict[str, Any]:
    previous = tel.get_run_context()
    tel.set_run_context(run.run_id, "run")
    try:
        result = run.client.chat([], max_tokens=REQUEST_OUTPUT_LIMIT)
        return {"outcome": "success", "content_matches_fixture": result == FIXTURE_CONTENT}
    except Exception as exc:
        # No response bodies, exception messages, URLs or request objects leave
        # the fixture. Expected failures are identified by their public class.
        return {"outcome": type(exc).__name__, "content_matches_fixture": None}
    finally:
        tel.set_run_context(*previous)


def _accounting(run) -> dict[str, Any]:
    snapshot = tel.LLMMeter.cumulative_snapshot(run.run_id)
    with sqlite3.connect(run.ledger_path) as connection:
        owners = connection.execute(
            "SELECT COUNT(DISTINCT owner_attempt_id) FROM usage_operations WHERE run_id=?",
            (run.run_id,),
        ).fetchone()[0]
    return {
        "recorded_calls": snapshot["total"]["calls"],
        "recorded_tokens": snapshot["total"]["total_tokens"],
        "unknown_operations": snapshot["api_operation_state"]["unknown"],
        "in_flight_operations": snapshot["api_operation_state"]["in_flight"],
        "distinct_owner_attempts": owners,
        "token_reservation_state": snapshot["token_reservation_state"],
    }


def _concurrent_case(directory: Path, label: str, baseline=None) -> dict[str, Any]:
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    requests = 0
    active = 0
    peak = 0

    def transport(request):
        nonlocal requests, active, peak
        with lock:
            requests += 1
            first = requests == 1
            active += 1
            peak = max(peak, active)
        try:
            if first:
                entered.set()
                if not release.wait(timeout=15):
                    raise TimeoutError("Offline concurrency fixture release timed out")
            return httpx.Response(200, json=_response())
        finally:
            with lock:
                active -= 1

    with _run(directory, label + "-concurrent", 20, transport, baseline) as run:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(_invoke, run)
            try:
                if not entered.wait(timeout=10):
                    raise RuntimeError("First offline request did not reach the transport")
                pending = _accounting(run)
                second_outcome = _invoke(run)
            finally:
                release.set()
            first_outcome = first.result(timeout=10)
        result = {"token_cap": 20, "max_tokens": REQUEST_OUTPUT_LIMIT,
                  "mock_transport_requests": requests, "peak_fixture_transports": peak,
                  "first_request": first_outcome, "competing_request": second_outcome,
                  "while_first_request_pending": pending,
                  **_accounting(run)}
        expected_requests = 2 if baseline is not None else 1
        assert requests == peak == expected_requests, result
        assert result["recorded_calls"] == expected_requests, result
        assert result["recorded_tokens"] == 15 * expected_requests, result
        assert result["distinct_owner_attempts"] == 1, result
        assert pending["in_flight_operations"] == 1, result
        assert pending["token_reservation_state"]["reserved_tokens"] == (0 if baseline is not None else 14), result
        assert result["token_reservation_state"]["reserved_tokens"] == 0, result
        assert second_outcome["outcome"] == ("success" if baseline is not None else "BudgetExceeded"), result
        assert first_outcome["outcome"] == ("BudgetExceeded" if baseline is not None else "success"), result
        return result


def _timeout_case(directory: Path, label: str, baseline=None) -> dict[str, Any]:
    requests = 0

    def transport(request):
        nonlocal requests
        requests += 1
        raise httpx.ReadTimeout("Offline fixture timeout", request=request)

    with _run(directory, label + "-timeout", 28, transport, baseline) as run:
        outcome = _invoke(run)
        result = {"token_cap": 28, "max_tokens": REQUEST_OUTPUT_LIMIT,
                  "mock_transport_requests": requests, **outcome, **_accounting(run)}
        expected_requests = 3 if baseline is not None else 2
        assert requests == result["recorded_calls"] == result["unknown_operations"] == expected_requests, result
        assert result["recorded_tokens"] == result["in_flight_operations"] == 0, result
        assert result["token_reservation_state"]["reserved_tokens"] == (0 if baseline is not None else 28), result
        assert result["token_reservation_state"]["active_operations"] == (0 if baseline is not None else 2), result
        assert outcome["outcome"] == ("APITimeoutError" if baseline is not None else "BudgetExceeded"), result
        return result


def _successful_case(directory: Path, baseline, samples: int) -> dict[str, Any]:
    requests = {"before": 0, "after": 0}

    def transport_for(label):
        def transport(request):
            requests[label] += 1
            return httpx.Response(200, json=_response())
        return transport

    cap = 1000000
    with ExitStack() as stack:
        runs = {label: stack.enter_context(_run(directory, label + "-success", cap,
                                                transport_for(label), method))
                for label, method in (("before", baseline), ("after", None))}
        timings = {label: [] for label in runs}
        for run in runs.values():
            for _ in range(WARMUP_REQUESTS):
                assert _invoke(run) == {"outcome": "success", "content_matches_fixture": True}
        for sample in range(samples):
            labels = ("before", "after") if sample % 2 == 0 else ("after", "before")
            for label in labels:
                start = time.perf_counter()
                outcome = _invoke(runs[label])
                timings[label].append((time.perf_counter() - start) * 1000)
                assert outcome == {"outcome": "success", "content_matches_fixture": True}
        result = {}
        for label, run in runs.items():
            result[label] = {"token_cap": cap, "max_tokens": REQUEST_OUTPUT_LIMIT,
                             "mock_transport_requests": requests[label], "samples": samples,
                             "untimed_warmup_requests": WARMUP_REQUESTS,
                             "median_local_elapsed_ms": round(statistics.median(timings[label]), 4),
                             "all_content_matches_fixture": True, **_accounting(run)}
            assert requests[label] == samples + WARMUP_REQUESTS
            assert result[label]["recorded_calls"] == requests[label]
            assert result[label]["recorded_tokens"] == 15 * requests[label]
            assert result[label]["unknown_operations"] == result[label]["in_flight_operations"] == 0
            assert result[label]["token_reservation_state"]["reserved_tokens"] == 0
        return result


def _source_hashes() -> dict[str, str]:
    paths = [Path(lc.__file__), Path(tel.__file__), Path(usage_ledger.__file__),
             BACKEND / "app/utils/api_budget.py", Path(__file__)]
    return {str(path.relative_to(BACKEND.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="5924dd4")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error("samples must be between 1 and 1000")
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
        cwd=BACKEND, text=True,
    ).strip()
    baseline, baseline_hash, method_hash = _load_baseline(revision)
    hashes = _source_hashes()
    disabled = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with ExitStack() as stack:
            for key, value in {"LLM_CACHE_ENABLED": False, "LLM_TIERED_ROUTING": False,
                               "LLM_TELEMETRY_ENABLED": True, "LLM_RUN_BUDGET_USD": 0}.items():
                stack.enter_context(patch.object(Config, key, value))
            stack.enter_context(patch.dict("os.environ", {"LLM_FALLBACK_PROVIDER": ""}))
            stack.enter_context(patch.object(lc, "_CB_STATE", {}))
            stack.enter_context(patch.object(lc.time, "sleep", lambda _: None))
            temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="astra-reservation-comparison-"))
            directory = Path(temporary)
            cases = {
                "same_owner_concurrency": {label: _concurrent_case(directory, label, method)
                                           for label, method in (("before", baseline), ("after", None))},
                "timeout_retries": {label: _timeout_case(directory, label, method)
                                    for label, method in (("before", baseline), ("after", None))},
                "successful_request_overhead": _successful_case(directory, baseline, args.samples),
            }
    finally:
        logging.disable(disabled)
    if _source_hashes() != hashes:
        raise RuntimeError("Source changed during the benchmark; rerun after implementation settles")
    receipt = {
        "schema": "astra-reservation-comparison/v1", "baseline_commit": revision,
        "baseline_llm_client_sha256": baseline_hash, "baseline_method_ast_sha256": method_hash,
        "current_source_sha256": hashes,
        "runtime_versions": {name: version(name) for name in ("openai", "httpx")},
        "provider_requests": 0, "network_requests": 0,
        "method": "Only the pinned LLMClient._create_openai_completion method is bound to baseline clients. Both sides use the current outer retry loop, meter, full projection and ledger schema in fresh temporary runs.",
        "fixture_request": {"messages_count": 0, "max_tokens": REQUEST_OUTPUT_LIMIT,
                            "estimated_input_tokens": 4, "planned_tokens_per_attempt": 14},
        "timing_scope": "Only successful synchronous chat calls are timed, with three untimed warm-ups per side and alternating order. Samples include durable admission/settlement writes. Concurrency and timeout cases have no latency claim.",
        "cases": cases,
        "limits": [
            "Synthetic fixtures prove admission and usage preservation, not production latency, historical overshoot or invoiced savings.",
            "Budget caps are enabled for this comparison; default-disabled guards have no claimed benefit.",
            "The baseline method writes no allowance policy or holds; the current method activates them in otherwise identical current-schema storage.",
            "Empty messages estimate four input tokens but the fixture reports ten. Reservations are planned allowance, not guaranteed provider tokenization or a complete spend ceiling.",
            "Timeouts have unknown usage. Zero recorded tokens are not evidence of zero consumption.",
            "This harness covers shared synchronous LLMClient dispatch. Native asynchronous, process/restart, malformed usage and compatibility cases require separate regressions.",
            "No CLI subprocess, paid provider, deployment or original saved-run mutation occurs.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
