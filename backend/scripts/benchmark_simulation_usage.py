#!/usr/bin/env python3
"""Offline native simulation accounting comparison using the actual SDK.

Both factories receive the same fixture model and current durable parent meter.
This isolates direct CAMEL transport capture; it does not simulate a deployment
or charge old compatibility snapshots. Separate process regressions verify the
new child bootstrap and parent compatibility import.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import logging
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.config import Config  # noqa: E402
from app.utils import oasis_llm, telemetry  # noqa: E402


class ParsedFixture(BaseModel):
    value: int


def _body(content="fixture", inputs=10, outputs=5):
    return {"id": "offline", "object": "chat.completion", "created": 1, "model": "offline",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": inputs, "completion_tokens": outputs, "total_tokens": inputs + outputs}}


def _case(module, directory, label, scenario, samples):
    requests = 0

    def transport(request):
        nonlocal requests
        requests += 1
        if scenario == "rate_limit_then_success" and requests == 1:
            return httpx.Response(429, headers={"retry-after-ms": "1"},
                                  json={"error": {"message": "offline", "type": "rate_limit"}})
        return httpx.Response(200, json=_body("" if scenario == "empty_response" else "fixture"))

    run = f"offline-{label}-{scenario}"
    old = telemetry.get_run_context()
    telemetry.LLMMeter.attach_durable_run(run, str(directory / (run + ".sqlite3")),
                                         "parent", default_stage="run", operation_scope="fixture")
    telemetry.set_run_context(run, "run")
    sdk = OpenAI(api_key="fixture", base_url="https://offline.invalid/v1", max_retries=3,
                 http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    async_sdk = AsyncOpenAI(api_key="fixture", base_url="https://offline.invalid/v1", max_retries=3,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    model = types.SimpleNamespace(_client=sdk, _async_client=async_sdk, model_type="offline", model_config_dict={})
    model._request_chat_completion = lambda messages, tools=None: sdk.chat.completions.create(
        model="offline", messages=messages, **({"tools": tools} if tools else {}))
    elapsed = []
    outcome = "success"
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_BOOST_API_KEY": "",
                                                           "SIM_LLM_FALLBACK": "false"}))
            stack.enter_context(patch.object(module, "_create_openai_model", lambda *a, **k: model))
            for key, value in {"LLM_RUN_BUDGET_TOKENS": 0, "LLM_RUN_BUDGET_USD": 0,
                               "LLM_TELEMETRY_ENABLED": True}.items():
                stack.enter_context(patch.object(Config, key, value))
            module.create_oasis_model({"llm_provider": "openai"})
            for _ in range(samples if scenario == "successful_request_overhead" else 1):
                start = time.perf_counter()
                try:
                    if scenario == "parse_failure":
                        sdk.beta.chat.completions.parse(model="offline", messages=[], response_format=ParsedFixture)
                    else:
                        model._request_chat_completion([])
                except Exception as exc:
                    outcome = type(exc).__name__
                elapsed.append((time.perf_counter() - start) * 1000)
            snap = telemetry.LLMMeter.cumulative_snapshot(run)
            return {"mock_transport_requests": requests, "recorded_calls": snap["total"]["calls"],
                    "recorded_tokens": snap["total"]["total_tokens"],
                    "unknown_operations": snap["api_operation_state"]["unknown"], "outcome": outcome,
                    "median_local_elapsed_ms": (round(statistics.median(elapsed), 4)
                                                if scenario == "successful_request_overhead" else None),
                    "samples": len(elapsed)}
    finally:
        sdk.close()
        import asyncio
        asyncio.run(async_sdk.close())
        telemetry.LLMMeter.reset(run)
        telemetry.set_run_context(*old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="b9a9f79")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 10000:
        parser.error("samples must be between1 and10000")
    revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], cwd=BACKEND, text=True).strip()
    source = subprocess.check_output(["git", "show", revision + ":backend/app/utils/oasis_llm.py"], cwd=BACKEND, text=True)
    baseline = types.ModuleType("app.utils._astra_baseline_oasis")
    baseline.__file__ = "<local-git-baseline>"
    exec(compile(source, baseline.__file__, "exec"), baseline.__dict__)
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with tempfile.TemporaryDirectory(prefix="astra-sim-comparison-") as temporary:
            cases = {scenario: {label: _case(module, Path(temporary), label, scenario, args.samples)
                                for label, module in (("before", baseline), ("after", oasis_llm))}
                     for scenario in ("rate_limit_then_success", "empty_response", "parse_failure", "successful_request_overhead")}
    finally:
        logging.disable(disabled)
    receipt = {"schema": "astra-simulation-comparison/v1", "baseline_commit": revision,
               "baseline_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "current_source_sha256": {str(path.relative_to(BACKEND.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in (Path(oasis_llm.__file__), BACKEND / "app/utils/oasis_usage.py", Path(__file__))},
               "provider_requests": 0, "network_requests": 0,
               "transport": "Actual synchronous OpenAI SDK calls over httpx.MockTransport; both factories receive same fixture model and current durable meter. Async calls are covered by separate regressions.",
               "timing_scope": "Only repeated successful-request medians are reported; one-sample scenarios are cold/order-sensitive and have no timing comparison.",
               "cases": cases,
               "limits": "Isolates native transport capture before compatibility export. Both modes retain SDK retries. Local elapsed overhead is not production latency or a workflow speedup; failed CLI subprocess usage and hard reservations remain outside coverage."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
