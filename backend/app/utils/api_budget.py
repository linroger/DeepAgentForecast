"""Plan text API token allowance without retaining request content.

The input estimate is deliberately labeled: provider tokenization, framing and
reasoning may differ. An explicit output limit bounds the requested output;
neither this estimate nor a reservation is an invoice guarantee.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .telemetry import BudgetExceeded, LLMMeter


def _enabled_limit(run_id: str) -> int | None:
    from ..config import Config
    try:
        limit = int(getattr(Config, "LLM_RUN_BUDGET_TOKENS", 0) or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BudgetExceeded("Cannot reserve API token allowance: invalid configured token limit") from exc
    if limit <= 0 or not LLMMeter.is_durable_run(run_id):
        return None
    if limit > 2**53:
        raise BudgetExceeded("Cannot reserve API token allowance: configured token limit exceeds supported range")
    return limit


def prepare_token_request(request: dict[str, Any], run_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Pin the enablement decision and isolate shared mutable SDK arguments."""
    limit = _enabled_limit(run_id)
    if limit is None:
        return request, None
    try:
        copied = deepcopy(request)
    except (ValueError, TypeError, RecursionError) as exc:
        raise BudgetExceeded("Cannot reserve API token allowance: request snapshot failed") from exc
    return copied, _plan_token_reservation(copied, limit)


def plan_token_reservation(request: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """Plan an already serialized, request-local native SDK payload."""
    limit = _enabled_limit(run_id)
    return _plan_token_reservation(request, limit) if limit is not None else None


def _plan_token_reservation(request: dict[str, Any], limit: int) -> dict[str, Any]:

    def reject(reason: str) -> None:
        raise BudgetExceeded(f"Cannot reserve API token allowance: {reason}")

    # The SDK merges extra_body after ordinary arguments. Permit only the
    # existing reasoning controls, never an override of planned input or caps.
    extra = request.get("extra_body")
    if extra is not None and (not isinstance(extra, dict) or
                              set(extra) - {"thinking", "enable_thinking"}):
        reject("extra request fields cannot override the planned text request")
    if request.get("web_search_options") is not None or request.get("prediction") is not None:
        reject("hosted search or prediction needs a separate allowance policy")
    caps = [request[key] for key in ("max_tokens", "max_completion_tokens")
            if request.get(key) is not None]
    if (len(caps) != 1 or isinstance(caps[0], bool) or not isinstance(caps[0], int)
            or not 0 < caps[0] <= 2**53):
        reject("set exactly one positive integer output limit")
    if type(request.get("n", 1)) is not int or request.get("n", 1) != 1:
        reject("only one completion per request is supported")
    if request.get("stream", False) is not False:
        reject("streaming usage is not supported by this reservation boundary")
    if request.get("audio") or request.get("modalities", ["text"]) not in (None, ["text"]):
        reject("only text output is supported")
    messages = request.get("messages")
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        reject("messages must be text chat objects")
    for message in messages:
        content = message.get("content")
        if message.get("audio"):
            reject("audio input needs a separate allowance policy")
        if content is not None and not isinstance(content, str):
            if (not isinstance(content, list) or any(
                    not isinstance(part, dict) or part.get("type") != "text"
                    or not isinstance(part.get("text"), str) for part in content)):
                reject("nontext input needs a separate allowance policy")
    tools = request.get("tools")
    if tools is not None and (not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") != "function"
            or not isinstance(tool.get("function"), dict) for tool in tools)):
        reject("only client-defined function tools are supported")
    inputs = {key: request[key] for key in (
        "messages", "tools", "functions", "response_format", "tool_choice", "function_call")
        if request.get(key) is not None}
    try:
        serialized = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        estimated_input = (len(serialized.encode("utf-8")) + 3) // 4
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        reject("request input must have a finite JSON representation")
    return {"token_limit": limit, "prompt_tokens_estimate": estimated_input,
            "completion_tokens_limit": caps[0], "estimator": "utf8-json-quarter/v1"}
