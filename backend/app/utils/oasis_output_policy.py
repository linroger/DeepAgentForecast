"""Explicit native OASIS output options, separate from model/provider routing."""

from __future__ import annotations

from typing import Any


SCHEMA = "oasis-output-policy/v1"


def validate_output_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate a metadata-only policy and return an independent value."""
    if not isinstance(policy, dict) or set(policy) != {"schema", "max_output_tokens", "parameter"}:
        raise ValueError("Native output policy must contain schema, max_output_tokens and parameter")
    if policy["schema"] != SCHEMA:
        raise ValueError("Unsupported native output policy schema")
    cap = policy["max_output_tokens"]
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 2**53:
        raise ValueError("OASIS_MAX_OUTPUT_TOKENS must be an integer from 0 through 2**53")
    if policy["parameter"] not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("OASIS_OUTPUT_TOKEN_PARAMETER must be max_tokens or max_completion_tokens")
    return dict(policy)


def configured_output_policy() -> dict[str, Any]:
    """Read Config only; child launch authority can override ambient settings."""
    from ..config import Config
    return validate_output_policy({"schema": SCHEMA, "max_output_tokens": Config.OASIS_MAX_OUTPUT_TOKENS,
                                   "parameter": Config.OASIS_OUTPUT_TOKEN_PARAMETER})


def model_output_policy() -> dict[str, Any]:
    """Prefer an explicitly bound launch policy, including a pinned zero cap."""
    from .simulation_usage import current_output_policy
    from .telemetry import BudgetExceeded
    try:
        bound = current_output_policy()
        return validate_output_policy(bound) if bound is not None else configured_output_policy()
    except (TypeError, ValueError) as exc:
        raise BudgetExceeded(f"Invalid native output policy: {exc}") from exc
