"""Captured API price estimates, never provider invoices or token upper bounds.

Quotes price inclusive input at one rate without inferring cache discounts.
They contain attribution and numbers only, and can be replayed without Config.
"""

from __future__ import annotations

from functools import lru_cache
import json
import math
from typing import Any


SCHEMA = "api-cost-quote/v1"
_FIELDS = {"schema", "provider", "model", "source", "rate_key",
           "input_usd_per_1k", "output_usd_per_1k"}
_SOURCES = {"configured_model", "configured_provider", "builtin_provider", "unpriced"}


def _rate(value: Any) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 2**53 or not math.isfinite(value)):
        raise ValueError("API prices must be finite nonnegative numbers no larger than 2**53")
    return float(value)


def validate_cost_quote(quote: Any, *, provider: str | None = None,
                        model: str | None = None) -> dict[str, Any]:
    """Validate strict replay metadata and return independent canonical values."""
    if not isinstance(quote, dict) or set(quote) != _FIELDS or quote.get("schema") != SCHEMA:
        raise ValueError("Invalid API cost quote schema")
    result = dict(quote)
    for key, expected in (("provider", provider), ("model", model)):
        value = result[key]
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError("API cost quote requires explicit provider and model")
        if expected is not None and value != expected:
            raise ValueError("API cost quote attribution mismatch")
    source = result["source"]
    if not isinstance(source, str) or source not in _SOURCES:
        raise ValueError("Invalid API cost quote source")
    if source == "unpriced":
        if any(result[k] is not None for k in ("rate_key", "input_usd_per_1k", "output_usd_per_1k")):
            raise ValueError("Unpriced API quote must retain unknown rates")
    else:
        expected_key = result["provider"].strip().lower()
        if source == "configured_model":
            expected_key += ":" + result["model"]
        if result["rate_key"] != expected_key:
            raise ValueError("API cost quote rate key mismatch")
        for key in ("input_usd_per_1k", "output_usd_per_1k"):
            result[key] = _rate(result[key])
    return result


@lru_cache(maxsize=128)
def _configured_rates(raw: str) -> tuple[tuple[str, tuple[float, float]], ...]:
    """Cache only immutable validated pairs; duplicate normalized keys fail."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate API price key")
            result[key] = value
        return result

    parsed = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(parsed, dict):
        raise ValueError("LLM_COST_PER_MTOK must be a JSON object")
    rates = {}
    for key, value in parsed.items():
        key = key.strip()
        provider, separator, model = key.partition(":")
        if not provider or len(provider) > 512 or (separator and (not model or len(model) > 512)):
            raise ValueError("API price key must identify a provider or provider:model")
        normalized = provider.lower() + (":" + model if separator else "")
        if normalized in rates:
            raise ValueError("Duplicate normalized API price key")
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("API price requires exactly two numbers in dollars per million tokens")
        rates[normalized] = (_rate(value[0]) / 1000.0, _rate(value[1]) / 1000.0)
    return tuple(rates.items())


def capture_cost_quote(provider: str, model: str) -> dict[str, Any]:
    """Resolve once before dispatch; dollar-enabled requests require a price."""
    from ..config import Config
    from .telemetry import BudgetExceeded, _COST_PER_1K

    try:
        raw = Config.LLM_COST_PER_MTOK or ""
        if not isinstance(raw, str):
            raise ValueError("LLM_COST_PER_MTOK must be JSON text")
        configured = dict(_configured_rates(raw.strip())) if raw.strip() else {}
        key = provider.strip().lower()
        model_key = key + ":" + model
        if model_key in configured:
            source, rate_key, rates = "configured_model", model_key, configured[model_key]
        elif key in configured:
            source, rate_key, rates = "configured_provider", key, configured[key]
        else:
            builtin = _COST_PER_1K.get(key)
            # Built-in zero pairs are subscription/unknown-price placeholders,
            # not evidence that a physical API request has a free price.
            if builtin is not None and any(builtin):
                source, rate_key, rates = "builtin_provider", key, builtin
            else:
                source, rate_key, rates = "unpriced", None, (None, None)
        quote = validate_cost_quote({"schema": SCHEMA, "provider": provider, "model": model,
            "source": source, "rate_key": rate_key,
            "input_usd_per_1k": rates[0], "output_usd_per_1k": rates[1]})
        limit = Config.LLM_RUN_BUDGET_USD
        if (isinstance(limit, bool) or not isinstance(limit, (int, float))
                or not math.isfinite(limit) or limit < 0):
            raise ValueError("Dollar budget must be finite and nonnegative")
        if limit > 0 and source == "unpriced":
            raise ValueError("Dollar-enabled API request has no price; configure LLM_COST_PER_MTOK")
        return quote
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise BudgetExceeded(f"Cannot price API request: {exc}") from exc


def quote_cost(quote: dict[str, Any], prompt_tokens: int, completion_tokens: int) -> float:
    """Compute an estimate from the stored quote, with the legacy float order."""
    quote = validate_cost_quote(quote)
    for count in (prompt_tokens, completion_tokens):
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 2**53:
            raise ValueError("Quoted usage must be nonnegative integer token counts")
    if quote["source"] == "unpriced":
        return 0.0
    value = ((prompt_tokens / 1000.0) * quote["input_usd_per_1k"]
             + (completion_tokens / 1000.0) * quote["output_usd_per_1k"])
    if not math.isfinite(value) or value > 2**53:
        raise ValueError("Quoted cost exceeds supported accounting range")
    return value
