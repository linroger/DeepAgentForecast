"""Durable non-streaming CAMEL accounting at each SDK HTTP send boundary.

No request/response bodies, headers, URLs, or credentials enter the ledger.
SDK retries retain their existing policy and receive distinct operation IDs.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from .llm_client import LLMClient
from .api_budget import plan_token_reservation
from .api_cost import capture_cost_context
from .telemetry import (
    BudgetExceeded, LLMMeter, UsageLedgerConflict, UsageLedgerStorageError,
    UsageLedgerUnresolvedError, check_budget, resolve_run_attribution,
)


_CONTROL_ERRORS = (BudgetExceeded, UsageLedgerStorageError, UsageLedgerConflict)


class _AccountingStop(BaseException):
    """Cross the SDK's Exception retry catch, then restore the original error.

    The SDK treats exceptions from httpx.send as retryable connection failures.
    SDK.request wrappers unwrap this private carrier outside that retry loop.
    """

    def __init__(self, error: Exception):
        self.error = error


class _Attempt:
    def __init__(self, request: Any, provider: str, expected_run: str):
        self.run_id, self.stage, self.inferred = resolve_run_attribution()
        if self.run_id != expected_run or not LLMMeter.is_durable_run(self.run_id):
            raise UsageLedgerStorageError("Bound OASIS request lost its durable run context")
        LLMMeter.assert_accounting_available(self.run_id)
        check_budget(self.run_id)
        try:
            body = json.loads(request.content)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise UsageLedgerStorageError("Durable OASIS requires a JSON chat request") from exc
        if not isinstance(body, dict) or body.get("stream", False) is not False:
            raise UsageLedgerStorageError("Durable OASIS accounting requires non-streaming chat requests")
        self.model = body.get("model")
        self.messages = body.get("messages")
        if (not isinstance(self.model, str) or not self.model or len(self.model) > 512
                or not isinstance(self.messages, list)
                or any(not isinstance(message, dict) for message in self.messages)):
            raise UsageLedgerStorageError("Durable OASIS requires model and message attribution")
        self.provider = provider
        self.operation_id = LLMMeter.new_operation_id(self.run_id)
        self.reservation = plan_token_reservation(body, self.run_id)
        self.cost_quote, dollar_enabled = capture_cost_context(provider, self.model)
        self.require_cost_coverage = dollar_enabled and LLMMeter.is_durable_run(self.run_id)
        self.record(calls=0, status="in_flight")
        self.started = time.monotonic()

    def record(self, *, calls: int = 1, status: str = "completed", latency_ms: float = 0,
               prompt_tokens: int = 0, completion_tokens: int = 0,
               usage_source: str = "unknown", **cache: Any) -> None:
        LLMMeter.record_snapshot(
            "llm_api_attempt", self.operation_id, self.provider, self.model,
            prompt_tokens, completion_tokens, latency_ms,
            run_id=self.run_id, stage=self.stage, _fallback=self.inferred,
            calls=calls, status=status, usage_source=usage_source,
            uncached_tokens=None, **cache,
            token_reservation=self.reservation if status == "in_flight" else None,
            cost_quote=self.cost_quote,
            require_cost_coverage=self.require_cost_coverage if status == "in_flight" else False,
        )

    def unknown(self) -> None:
        self.record(status="unknown", latency_ms=(time.monotonic() - self.started) * 1000)
        self.check_budget()

    def check_budget(self) -> None:
        if self.reservation is not None:
            check_budget(self.run_id, token_limit=self.reservation["token_limit"])
        else:
            check_budget(self.run_id)

    def settle(self, response: Any) -> None:
        elapsed = (time.monotonic() - self.started) * 1000
        try:
            body = response.json()
        except (ValueError, UnicodeError) as exc:
            if not response.is_success:
                self.unknown()
                return
            self.record(status="accounting_error", latency_ms=elapsed)
            raise UsageLedgerUnresolvedError("OASIS response omitted readable usage; precise settlement required") from exc
        # Error pages are not generated model output and cannot support text
        # estimates. Retain reported usage when a failed response provides it.
        if not response.is_success and (not isinstance(body, dict) or body.get("usage") is None):
            self.unknown()
            return
        try:
            if not isinstance(body, dict):
                raise ValueError("OASIS completion response must be an object")
            usage = LLMClient._response_usage(body, self.messages)
        except (ValueError, TypeError, OverflowError, AttributeError) as exc:
            self.record(status="accounting_error", latency_ms=elapsed)
            raise UsageLedgerUnresolvedError("Invalid OASIS usage; precise settlement required") from exc
        # Completed means a response's usage was settled before SDK parsing; it
        # does not assert that tool arguments or structured output are valid.
        self.record(status="completed" if response.is_success else "unknown", latency_ms=elapsed, **usage)
        self.check_budget()


def _instrument_client(sdk: Any, provider: str, expected_run: str, *, asynchronous: bool) -> None:
    identity = (provider, expected_run)
    http = getattr(sdk, "_client", None)
    if not callable(getattr(http, "send", None)) or not callable(getattr(sdk, "request", None)):
        raise UsageLedgerStorageError("CAMEL client lacks the required durable HTTP accounting boundary")
    installed = getattr(http, "_drf_oasis_usage_identity", None)
    if installed is not None and installed != identity:
        raise UsageLedgerConflict("HTTP client already belongs to another OASIS accounting route")
    if installed is None:
        original_send = http.send
        if asynchronous:
            async def send(request: Any, *args: Any, **kwargs: Any) -> Any:
                response = None
                try:
                    attempt = _Attempt(request, provider, expected_run)
                    try:
                        response = await original_send(request, *args, **kwargs)
                        await response.aread()
                    except _CONTROL_ERRORS:
                        raise
                    except Exception:
                        if response is not None:
                            with suppress(Exception):
                                await response.aclose()
                        attempt.unknown()
                        raise
                    attempt.settle(response)
                    return response
                except _CONTROL_ERRORS as exc:
                    if response is not None:
                        with suppress(Exception):
                            await response.aclose()
                    raise _AccountingStop(exc) from None
        else:
            def send(request: Any, *args: Any, **kwargs: Any) -> Any:
                response = None
                try:
                    attempt = _Attempt(request, provider, expected_run)
                    try:
                        response = original_send(request, *args, **kwargs)
                        response.read()
                    except _CONTROL_ERRORS:
                        raise
                    except Exception:
                        if response is not None:
                            with suppress(Exception):
                                response.close()
                        attempt.unknown()
                        raise
                    attempt.settle(response)
                    return response
                except _CONTROL_ERRORS as exc:
                    if response is not None:
                        with suppress(Exception):
                            response.close()
                    raise _AccountingStop(exc) from None
        http.send = send
        http._drf_oasis_usage_identity = identity

    installed_sdk = getattr(sdk, "_drf_oasis_usage_identity", None)
    if installed_sdk is not None:
        if installed_sdk != identity:
            raise UsageLedgerConflict("SDK client already belongs to another OASIS accounting route")
        return
    original_request = sdk.request
    if asynchronous:
        async def request(*args: Any, **kwargs: Any) -> Any:
            try:
                return await original_request(*args, **kwargs)
            except _AccountingStop as stop:
                raise stop.error from None
    else:
        def request(*args: Any, **kwargs: Any) -> Any:
            try:
                return original_request(*args, **kwargs)
            except _AccountingStop as stop:
                raise stop.error from None
    sdk.request = request
    sdk._drf_oasis_usage_identity = identity


def instrument_oasis_model(model: Any, provider: str) -> Any:
    """Instrument explicitly bound direct API models after client replacement."""
    run_id, _, _ = resolve_run_attribution()
    if not LLMMeter.is_durable_run(run_id):
        return model
    _instrument_client(getattr(model, "_client", None), provider, run_id, asynchronous=False)
    _instrument_client(getattr(model, "_async_client", None), provider, run_id, asynchronous=True)
    return model
