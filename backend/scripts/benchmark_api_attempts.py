#!/usr/bin/env python3
"""Compare API retry/accounting behavior using the real SDK with no network.

The baseline is loaded from a local Git revision into an isolated module. Both
versions use this checkout's telemetry and a temporary ledger. Backoff sleeps
are disabled; elapsed samples describe local accounting/SDK overhead only.
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
from openai import OpenAI

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Config  # noqa: E402
from app.utils import llm_client, telemetry  # noqa: E402


def _response(content="fixture", inputs=10, outputs=5):
    return {"id": "offline", "object": "chat.completion", "created": 1, "model": "offline",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": inputs, "completion_tokens": outputs, "total_tokens": inputs + outputs}}


def _case(module, directory, label, scenario, repetitions):
    requests = 0

    def transport(request):
        nonlocal requests
        requests += 1
        if scenario in {"rate_limit_chat", "rate_limit_native"}:
            return httpx.Response(429, json={"error": {"message": "offline rate limit", "type": "rate_limit"}})
        if scenario == "empty_then_success":
            body = _response("" if requests == 1 else "fixture", 100 if requests == 1 else 50,
                             20 if requests == 1 else 10)
        elif scenario == "empty_over_budget":
            body = _response("", 20, 5)
        else:
            body = _response()
        return httpx.Response(200, json=body)

    rid = f"offline-{label}-{scenario}"
    previous = telemetry.get_run_context()
    telemetry.LLMMeter.attach_durable_run(rid, str(directory / (rid + ".sqlite3")), attempt_id="fixture")
    telemetry.set_run_context(rid, "report")
    sdk = OpenAI(api_key="offline-fixture", base_url="https://offline.invalid/v1",
                 http_client=httpx.Client(transport=httpx.MockTransport(transport)), max_retries=2)
    samples = []
    outcome = "success"
    try:
        with ExitStack() as stack:
            for key, value in {"LLM_TELEMETRY_ENABLED": True, "LLM_CACHE_ENABLED": False,
                               "LLM_TIERED_ROUTING": False, "LLM_RUN_BUDGET_USD": 0,
                               "LLM_RUN_BUDGET_TOKENS": 10 if scenario == "empty_over_budget" else 0}.items():
                stack.enter_context(patch.object(Config, key, value))
            stack.enter_context(patch.dict("os.environ", {"LLM_FALLBACK_PROVIDER": ""}))
            stack.enter_context(patch.object(module, "_CB_STATE", {}))
            stack.enter_context(patch.object(module.time, "sleep", lambda _: None))
            stack.enter_context(patch.object(module.LLMClient, "_build_openai_client", staticmethod(lambda *a: sdk)))
            client = module.LLMClient(provider="openai", model="offline", api_key="offline-fixture")
            for _ in range(repetitions if scenario == "successful_request_overhead" else 1):
                started = time.perf_counter()
                try:
                    if scenario == "rate_limit_native":
                        client.chat_with_tools([], [])
                    else:
                        client.chat([])
                except Exception as exc:
                    outcome = type(exc).__name__
                samples.append((time.perf_counter() - started) * 1000)
            total = telemetry.LLMMeter.cumulative_snapshot(rid)["total"]
            return {"mock_transport_requests": requests, "recorded_calls": total["calls"],
                    "recorded_tokens": total["total_tokens"], "outcome": outcome,
                    "samples": len(samples), "median_local_elapsed_ms": round(statistics.median(samples), 3)}
    finally:
        sdk.close()
        telemetry.LLMMeter.reset(rid)
        telemetry.set_run_context(*previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="a1180cc")
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10000:
        parser.error("repetitions must be between 1 and 10000")
    revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
                                       cwd=BACKEND, text=True).strip()
    source = subprocess.check_output(["git", "show", revision + ":backend/app/utils/llm_client.py"],
                                     cwd=BACKEND, text=True)
    baseline = types.ModuleType("app.utils._astra_baseline_client")
    baseline.__file__ = "<local-git-baseline>"
    exec(compile(source, baseline.__file__, "exec"), baseline.__dict__)
    disabled_before = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with tempfile.TemporaryDirectory(prefix="astra-sdk-comparison-") as temporary:
            cases = {}
            for scenario in ("rate_limit_chat", "rate_limit_native", "empty_then_success",
                             "empty_over_budget", "successful_request_overhead"):
                cases[scenario] = {label: _case(module, Path(temporary), label, scenario, args.repetitions)
                                   for label, module in (("before", baseline), ("after", llm_client))}
    finally:
        logging.disable(disabled_before)
    receipt = {"schema": "astra-api-attempt-comparison/v1", "baseline_commit": revision,
               "source_sha256": {"before": hashlib.sha256(source.encode()).hexdigest(),
                                  "after": hashlib.sha256(Path(llm_client.__file__).read_bytes()).hexdigest()},
               "network_requests": 0, "provider_requests": 0, "transport": "httpx.MockTransport with actual OpenAI SDK",
               "backoff_sleeps": "disabled for both versions", "cases": cases,
               "limits": "Synthetic transport counts and local overhead only; not historical billing or production speedup. Baseline and changed client share current telemetry."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
