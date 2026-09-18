"""Explicit parent-owned accounting context for detached simulation processes.

Only metadata crosses this boundary. Children join an existing ledger/attempt;
compatibility simulation totals remain diagnostics and are never re-imported.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from .telemetry import LLMMeter, check_budget, set_run_context
from .usage_ledger import UsageLedgerConflict, UsageLedgerStorageError

CONTEXT_ENV = "DRF_SIMULATION_USAGE_CONTEXT"
SCHEMA = "simulation-shared-usage/v1"
_AUTH_KEYS = ("schema", "run_id", "attempt_id", "simulation_id", "launch_token")
_context: dict[str, Any] | None = None


def _output_policy(context: dict[str, Any]) -> dict[str, Any]:
    from .oasis_output_policy import SCHEMA as POLICY_SCHEMA, validate_output_policy
    try:
        return validate_output_policy(context.get("native_output_policy", {
            "schema": POLICY_SCHEMA, "max_output_tokens": 0, "parameter": "max_tokens",
        }))
    except (TypeError, ValueError) as exc:
        raise UsageLedgerConflict("Invalid simulation native output policy") from exc


def authority(context: dict[str, Any]) -> dict[str, Any]:
    result = {key: context[key] for key in _AUTH_KEYS}
    if "native_output_policy" in context:
        # Optional v1 extension: new receipts bind the complete launch policy,
        # while historical five-field receipts remain readable without upgrade.
        result.update(native_output_policy=_output_policy(context),
                      budget_tokens=context["budget_tokens"], budget_usd=context["budget_usd"])
    return result


def _validate(context: Any, config_path: str) -> dict[str, Any]:
    if not isinstance(context, dict) or context.get("schema") != SCHEMA:
        raise UsageLedgerConflict("Invalid simulation accounting context")
    for key in (*_AUTH_KEYS, "ledger_path", "pipeline_state_path"):
        value = context.get(key)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise UsageLedgerConflict("Incomplete simulation accounting lineage")
    path = Path(config_path).resolve()
    if path.parent.name != context["simulation_id"]:
        raise UsageLedgerConflict("Simulation accounting directory mismatch")
    for key in ("ledger_path", "pipeline_state_path"):
        if not Path(context[key]).is_absolute():
            raise UsageLedgerConflict("Accounting paths must be explicit and absolute")
    for key in ("budget_tokens", "budget_usd"):
        value = context.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise UsageLedgerConflict("Invalid simulation accounting budget")
        if key == "budget_tokens" and (not isinstance(value, int) or value > 2**53):
            raise UsageLedgerConflict("Invalid simulation token budget")
    try:
        state = json.loads(Path(context["pipeline_state_path"]).read_text(encoding="utf-8"))
        options = state.get("options") or {}
        expected = (options.get("simulation_usage_launches") or {}).get(context["launch_token"])
        if (state.get("pipeline_id") != context["run_id"]
                or options.get("usage_attempt_id") != context["attempt_id"]
                or expected != authority(context)):
            raise UsageLedgerConflict("Simulation launch is not owned by this parent attempt")
        # The parent storage path must be the one adjacent to its pipeline dirs.
        expected_path = Path(context["pipeline_state_path"]).parent.parent / "usage_ledger.sqlite3"
        if Path(context["ledger_path"]).resolve() != expected_path.resolve():
            raise UsageLedgerConflict("Simulation ledger does not match parent storage")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if cfg.get("simulation_id", context["simulation_id"]) != context["simulation_id"]:
            raise UsageLedgerConflict("Simulation config identity mismatch")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, UsageLedgerConflict):
            raise
        raise UsageLedgerStorageError("Simulation accounting lineage is unreadable") from exc
    checked = dict(context)
    if "native_output_policy" in context:
        checked["native_output_policy"] = _output_policy(context)
    return checked


def child_environment(env: dict[str, str], context: dict[str, Any] | None,
                      config_path: str) -> dict[str, str]:
    """Never carry another launch's ambient context into an unrelated child."""
    result = dict(env)
    result.pop(CONTEXT_ENV, None)
    if context is not None:
        checked = _validate(context, config_path)
        checked["config_sha256"] = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
        policy = _output_policy(checked)
        # Entry scripts import Config before bootstrap, so pin import-time values
        # in this private environment as well as the runtime authority below.
        result["OASIS_MAX_OUTPUT_TOKENS"] = str(policy["max_output_tokens"])
        result["OASIS_OUTPUT_TOKEN_PARAMETER"] = policy["parameter"]
        result[CONTEXT_ENV] = json.dumps(checked, separators=(",", ":"))
    return result


def bootstrap(config_path: str) -> dict[str, Any] | None:
    """Join only the validated launch's existing parent ledger before model work."""
    global _context
    raw = os.environ.get(CONTEXT_ENV)
    if raw is None:
        return None
    try:
        context = _validate(json.loads(raw), config_path)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, UsageLedgerConflict):
            raise
        raise UsageLedgerConflict("Malformed simulation accounting context") from exc
    digest = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    if context.get("config_sha256") != digest:
        raise UsageLedgerConflict("Simulation config changed after accounting admission")
    if _context is not None and _context != context:
        raise UsageLedgerConflict("Child already belongs to another launch")
    LLMMeter.attach_durable_run(context["run_id"], context["ledger_path"],
                               context["attempt_id"], existing_only=True, default_stage="run",
                               operation_scope=context["launch_token"])
    set_run_context(context["run_id"], "run")
    from app.config import Config
    Config.LLM_RUN_BUDGET_TOKENS = context["budget_tokens"]
    Config.LLM_RUN_BUDGET_USD = context["budget_usd"]
    policy = _output_policy(context)
    Config.OASIS_MAX_OUTPUT_TOKENS = policy["max_output_tokens"]
    Config.OASIS_OUTPUT_TOKEN_PARAMETER = policy["parameter"]
    LLMMeter.assert_accounting_available(context["run_id"])
    check_budget(context["run_id"])
    _context = context
    return authority(context)


def current_authority() -> dict[str, Any] | None:
    return authority(_context) if _context is not None else None


def current_output_policy() -> dict[str, Any] | None:
    """Return an independent pinned policy; legacy bound launches stay uncapped."""
    return _output_policy(_context) if _context is not None else None


def assert_complete() -> None:
    """Broad simulation degradation handlers cannot declare uncertain work done."""
    if _context is not None:
        LLMMeter.assert_local_accounting_settled(_context["run_id"])
        check_budget(_context["run_id"])
