"""Apply narrow, idempotent overlays to DeerFlow's embedded subagent path.

The vendor seed evolves independently of the active gitignored runtime. Never
copy complete client, task-tool, or executor modules across versions: doing so
can erase tracing, session, callback, and token-accounting behavior. This
transformer changes only three contracts while preserving surrounding bytes:

* embedded clients expose their exact ``AppConfig`` to runtime tools;
* ``model: inherit`` reads the active model from ``configurable`` when runtime
  metadata is absent (the embedded-client shape); and
* provider fallbacks and structurally all-denied evidence tasks terminate as
  failed instead of being wrapped as ``Task Succeeded``.

The existing application-wide subagent lifecycle lease remains part of this
same execution-path overlay.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


CLIENT_PATH = Path("backend/packages/harness/deerflow/client.py")
TASK_TOOL_PATH = Path(
    "backend/packages/harness/deerflow/tools/builtins/task_tool.py"
)
EXECUTOR_PATH = Path(
    "backend/packages/harness/deerflow/subagents/executor.py"
)

_CLIENT_CONTEXT_ORIGINAL = '        context = {"thread_id": thread_id}\n'
_CLIENT_CONTEXT_PATCHED = (
    '        # DRF overlay: task subagents must receive the exact per-client config;\n'
    '        # falling back to process-global config can select the first provider.\n'
    '        context = {"thread_id": thread_id, "app_config": self._app_config}\n'
)

# LOOP-017 F1: same-message-id usage snapshots are NOT always identical.
# ``TokenUsageAttributionMiddleware`` folds subagent spend into an already
# streamed AIMessage's cumulative ``usage_metadata``, so a later snapshot for
# the same id can be strictly larger (parent-only 10 → parent+subagents 110).
# The client's first-seen-wins dedup dropped that growth, undercounting
# Stage-1 research spend in ``cumulative_usage`` (the durable end-event
# total). The overlay tracks the counted amount per id and accounts only the
# positive delta; identical re-arrivals (values-vs-messages duplicates) still
# count zero, and negative movement is never subtracted.
_CLIENT_USAGE_MARKER = "DRF overlay: same-id usage snapshots are not always identical"
_CLIENT_USAGE_DECL_ORIGINAL = (
    "        # The same message id carries identical cumulative ``usage_metadata``\n"
    "        # in both the final ``messages`` chunk and the values snapshot —\n"
    "        # count it only on whichever arrives first.\n"
    "        counted_usage_ids: set[str] = set()\n"
)
_CLIENT_USAGE_DECL_PATCHED = (
    "        # DRF overlay: same-id usage snapshots are not always identical —\n"
    "        # the token-usage middleware folds subagent spend into an already\n"
    "        # streamed AIMessage's cumulative usage_metadata, so a later\n"
    "        # snapshot for the same id can be strictly larger. Track counted\n"
    "        # amounts per id and account only the positive delta.\n"
    "        counted_usage_by_id: dict[str, dict[str, int]] = {}\n"
)
_CLIENT_USAGE_BODY_ORIGINAL = (
    "            if not usage:\n"
    "                return None\n"
    "            if msg_id and msg_id in counted_usage_ids:\n"
    "                return None\n"
    "            if msg_id:\n"
    "                counted_usage_ids.add(msg_id)\n"
    "            input_tokens = usage.get(\"input_tokens\", 0) or 0\n"
    "            output_tokens = usage.get(\"output_tokens\", 0) or 0\n"
    "            total_tokens = usage.get(\"total_tokens\", 0) or 0\n"
    "            cumulative_usage[\"input_tokens\"] += input_tokens\n"
    "            cumulative_usage[\"output_tokens\"] += output_tokens\n"
    "            cumulative_usage[\"total_tokens\"] += total_tokens\n"
    "            return {\n"
    "                \"input_tokens\": input_tokens,\n"
    "                \"output_tokens\": output_tokens,\n"
    "                \"total_tokens\": total_tokens,\n"
    "            }\n"
)
_CLIENT_USAGE_BODY_PATCHED = (
    "            if not usage:\n"
    "                return None\n"
    "            input_tokens = usage.get(\"input_tokens\", 0) or 0\n"
    "            output_tokens = usage.get(\"output_tokens\", 0) or 0\n"
    "            total_tokens = usage.get(\"total_tokens\", 0) or 0\n"
    "            if msg_id:\n"
    "                prev = counted_usage_by_id.get(msg_id)\n"
    "                if prev is not None:\n"
    "                    delta_in = max(0, input_tokens - prev[\"input_tokens\"])\n"
    "                    delta_out = max(0, output_tokens - prev[\"output_tokens\"])\n"
    "                    delta_total = max(0, total_tokens - prev[\"total_tokens\"])\n"
    "                    if not (delta_in or delta_out or delta_total):\n"
    "                        return None\n"
    "                    prev[\"input_tokens\"] = max(prev[\"input_tokens\"], input_tokens)\n"
    "                    prev[\"output_tokens\"] = max(prev[\"output_tokens\"], output_tokens)\n"
    "                    prev[\"total_tokens\"] = max(prev[\"total_tokens\"], total_tokens)\n"
    "                    cumulative_usage[\"input_tokens\"] += delta_in\n"
    "                    cumulative_usage[\"output_tokens\"] += delta_out\n"
    "                    cumulative_usage[\"total_tokens\"] += delta_total\n"
    "                    return {\n"
    "                        \"input_tokens\": delta_in,\n"
    "                        \"output_tokens\": delta_out,\n"
    "                        \"total_tokens\": delta_total,\n"
    "                    }\n"
    "                counted_usage_by_id[msg_id] = {\n"
    "                    \"input_tokens\": input_tokens,\n"
    "                    \"output_tokens\": output_tokens,\n"
    "                    \"total_tokens\": total_tokens,\n"
    "                }\n"
    "            cumulative_usage[\"input_tokens\"] += input_tokens\n"
    "            cumulative_usage[\"output_tokens\"] += output_tokens\n"
    "            cumulative_usage[\"total_tokens\"] += total_tokens\n"
    "            return {\n"
    "                \"input_tokens\": input_tokens,\n"
    "                \"output_tokens\": output_tokens,\n"
    "                \"total_tokens\": total_tokens,\n"
    "            }\n"
)

# ASTRA-04b1 must upgrade already-installed LOOP-017 helpers as well as fresh
# vendor source. The original blocks above remain exact migration anchors.
_CLIENT_STREAM_USAGE_MARKER = "DRF overlay: checkpoint-relative stream usage v1"
_CLIENT_STREAM_USAGE_DECL = '''        # DRF overlay: checkpoint-relative stream usage v1
        counted_usage_by_id: dict[str, dict[str, Any]] = {}
        chunk_usage_by_id: dict[str, dict[str, Any]] = {}
        usage_stream_id = str(uuid.uuid4())
        usage_sequence = -1
        usage_baseline = "unverified"

        def _normalize_stream_usage(usage, *, check_cache_bounds=True):
            if usage is None:
                return None
            if not isinstance(usage, dict):
                raise RuntimeError("Invalid research usage metadata")
            def number(value):
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53:
                    raise RuntimeError("Invalid research usage counter")
                return value
            if "input_tokens" not in usage or "output_tokens" not in usage:
                raise RuntimeError("Research usage requires input and output counters")
            result = {key: number(usage[key]) for key in ("input_tokens", "output_tokens")}
            result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
            if "total_tokens" in usage and number(usage["total_tokens"]) != result["total_tokens"]:
                raise RuntimeError("Inconsistent research usage total")
            details = usage.get("input_token_details")
            if details is not None:
                if not isinstance(details, dict):
                    raise RuntimeError("Invalid research cache details")
                normalized = {}
                if "cache_read" in details:
                    normalized["cache_read"] = number(details["cache_read"])
                creation_keys = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
                if "cache_creation" in details or any(key in details for key in creation_keys):
                    # Anthropic's adapter may use TTL fields instead of the
                    # generic creation field. These describe the same tokens.
                    normalized["cache_creation"] = max(
                        number(details.get("cache_creation", 0)),
                        sum(number(details[key]) for key in creation_keys if key in details),
                    )
                if check_cache_bounds and sum(normalized.values()) > result["input_tokens"]:
                    raise RuntimeError("Research cache counters exceed inclusive input")
                if normalized:
                    result["input_token_details"] = normalized
            return result

        def _message_usage_snapshot(msg_id, message):
            from langchain_core.messages import AIMessageChunk

            is_chunk = isinstance(message, AIMessageChunk)
            current = _normalize_stream_usage(message.usage_metadata, check_cache_bounds=not is_chunk)
            if current is None or not is_chunk or not msg_id:
                return current
            # LangChain chunks add usage when combined. Values/AIMessage usage
            # is cumulative; first assemble the chunk-side snapshot, then let
            # the shared highwater deduplicate it against values snapshots.
            combined = chunk_usage_by_id.setdefault(msg_id, {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            })
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                combined[key] += current[key]
            if "input_token_details" in current:
                details = combined.setdefault("input_token_details", {})
                for key, value in current["input_token_details"].items():
                    details[key] = details.get(key, 0) + value
            return combined

        def _stream_usage_event(msg_id, usage, *, kind="delta"):
            nonlocal usage_sequence
            usage_sequence += 1
            return StreamEvent(type="usage", data={
                "schema": "research-stream-usage/v1", "stream_id": usage_stream_id,
                "sequence": usage_sequence, "usage": usage, "kind": kind,
                "thread_id": thread_id, "message_id": msg_id,
                "identity_stable": kind == "start" or bool(msg_id),
                "baseline": usage_baseline, "usage_complete": False,
                # Folded child totals need not preserve all child partitions.
                "cache_partition_known": False,
            })
'''
_CLIENT_STREAM_USAGE_BODY = '''            current = _normalize_stream_usage(usage)
            if current is None:
                return None
            if msg_id is not None and (not isinstance(msg_id, str) or not msg_id):
                raise RuntimeError("Invalid research usage message identity")
            previous = counted_usage_by_id.get(msg_id, {}) if msg_id else {}
            highwater = {key: max(previous.get(key, 0), current[key])
                         for key in ("input_tokens", "output_tokens")}
            highwater["total_tokens"] = highwater["input_tokens"] + highwater["output_tokens"]
            delta = {key: highwater[key] - previous.get(key, 0)
                     for key in ("input_tokens", "output_tokens", "total_tokens")}
            old_details = previous.get("input_token_details", {})
            details = dict(old_details)
            detail_delta = {}
            for key, value in current.get("input_token_details", {}).items():
                details[key] = max(old_details.get(key, 0), value)
                detail_delta[key] = details[key] - old_details.get(key, 0)
            if details:
                highwater["input_token_details"] = details
            if msg_id:
                counted_usage_by_id[msg_id] = highwater
            if not any(delta.values()) and not any(detail_delta.values()):
                return None
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                cumulative_usage[key] += delta[key]
            if detail_delta:
                delta["input_token_details"] = detail_delta
                total_details = cumulative_usage.setdefault("input_token_details", {})
                for key, value in detail_delta.items():
                    total_details[key] = total_details.get(key, 0) + value
            return delta
'''
_CLIENT_STREAM_LOOP = "        for item in self._agent.stream(\n"
_CLIENT_STREAM_BASELINE = '''        # Read the effective compiled graph's checkpoint before invoking it.
        # A configured default checkpointer may exist even if the client was
        # constructed without an explicit one.
        if not hasattr(self._agent, "checkpointer"):
            raise RuntimeError("Cannot determine research checkpoint usage baseline")
        if self._agent.checkpointer is None or self._agent.checkpointer is False:
            usage_baseline = "stateless"
        else:
            checkpoint = self._agent.get_state(config)
            baseline_values = getattr(checkpoint, "values", None)
            if not isinstance(baseline_values, dict):
                raise RuntimeError("Cannot read research checkpoint usage baseline")
            baseline_messages = baseline_values.get("messages", [])
            if not isinstance(baseline_messages, (list, tuple)):
                raise RuntimeError("Invalid research checkpoint messages")
            for baseline_message in baseline_messages:
                if not isinstance(baseline_message, AIMessage):
                    continue
                baseline_id = getattr(baseline_message, "id", None)
                baseline_usage = _normalize_stream_usage(getattr(baseline_message, "usage_metadata", None))
                if not isinstance(baseline_id, str) or not baseline_id or baseline_usage is None:
                    raise RuntimeError("Research checkpoint AI usage has unknown identity or counters")
                _account_usage(baseline_id, baseline_usage)
            cumulative_usage.clear()
            cumulative_usage.update(input_tokens=0, output_tokens=0, total_tokens=0)
            usage_baseline = "checkpoint"
        yield _stream_usage_event(None, dict(cumulative_usage), kind="start")

'''
_CLIENT_VALUES_ID = '''            for msg in messages:
                msg_id = getattr(msg, "id", None)
'''
_CLIENT_VALUES_USAGE = '''            # Account the entire observed frame before display can trigger a
            # corrective close. Later values can enrich already-displayed AI
            # messages with completed subagent usage.
            frame_usage = []
            for msg in messages:
                msg_id = getattr(msg, "id", None)
                counted_usage = None
                if isinstance(msg, AIMessage):
                    counted_usage = _account_usage(msg_id, getattr(msg, "usage_metadata", None))
                    if counted_usage is not None:
                        yield _stream_usage_event(msg_id, counted_usage)
                frame_usage.append(counted_usage)

            for msg, counted_usage in zip(messages, frame_usage, strict=True):
                msg_id = getattr(msg, "id", None)
'''
_CLIENT_MESSAGES_USAGE = "                    counted_usage = _account_usage(msg_id, msg_chunk.usage_metadata)\n"
_CLIENT_MESSAGES_EMISSION = '''                    counted_usage = _account_usage(msg_id, _message_usage_snapshot(msg_id, msg_chunk))
                    if counted_usage is not None:
                        yield _stream_usage_event(msg_id, counted_usage)
'''
_CLIENT_END_ORIGINAL = '        yield StreamEvent(type="end", data={"usage": cumulative_usage})\n'
_CLIENT_END_PATCHED = '''        yield StreamEvent(type="end", data={
            "usage": cumulative_usage, "usage_schema": "research-stream-usage/v1",
            "stream_id": usage_stream_id, "usage_complete": False,
        })
'''


def _patch_client_stream_usage(source: str, target: Path) -> str:
    """Upgrade the whole generator path, refusing partial/drifted overlays."""
    replacements = (
        (_CLIENT_USAGE_DECL_PATCHED, _CLIENT_STREAM_USAGE_DECL),
        (_CLIENT_USAGE_BODY_PATCHED, _CLIENT_STREAM_USAGE_BODY),
        (_CLIENT_STREAM_LOOP, _CLIENT_STREAM_BASELINE + _CLIENT_STREAM_LOOP),
        (_CLIENT_MESSAGES_USAGE, _CLIENT_MESSAGES_EMISSION),
        (_CLIENT_VALUES_ID, _CLIENT_VALUES_USAGE),
        ('                        _account_usage(msg_id, getattr(msg, "usage_metadata", None))\n', ''),
        ('                    counted_usage = _account_usage(msg_id, msg.usage_metadata)\n', ''),
        (_CLIENT_END_ORIGINAL, _CLIENT_END_PATCHED),
        ('StreamEventType = Literal["values", "messages-tuple", "custom", "end"]',
         'StreamEventType = Literal["values", "messages-tuple", "custom", "usage", "end"]'),
    )
    if _CLIENT_STREAM_USAGE_MARKER in source:
        required = (_CLIENT_STREAM_USAGE_DECL, _CLIENT_STREAM_USAGE_BODY,
                    _CLIENT_STREAM_BASELINE + _CLIENT_STREAM_LOOP,
                    _CLIENT_MESSAGES_EMISSION,
                    _CLIENT_VALUES_USAGE, _CLIENT_END_PATCHED,
                    replacements[-1][1])
        if any(source.count(block) != 1 for block in required):
            raise RuntimeError(f"client stream usage overlay is partial or drifted: {target}")
        return source
    for original, patched in replacements:
        if source.count(original) != 1:
            raise RuntimeError(f"client stream usage overlay context drifted: {target}")
        source = source.replace(original, patched, 1)
    return source

_TASK_MODEL_ORIGINAL = '        parent_model = metadata.get("model_name")\n'
_TASK_MODEL_PATCHED = (
    '        # DRF overlay: embedded clients carry the active model under\n'
    '        # RunnableConfig.configurable. Metadata may be empty when tracing is off.\n'
    '        configurable = runtime.config.get("configurable", {})\n'
    '        parent_model = (\n'
    '            metadata.get("model_name")\n'
    '            or configurable.get("model_name")\n'
    '            or configurable.get("model")\n'
    '        )\n'
)

_ORIGINAL_SIGNATURE = (
    "    async def _aexecute(self, task: str, result_holder: "
    "SubagentResult | None = None) -> SubagentResult:\n"
)
_LEASED_SIGNATURE = (
    "    async def _aexecute_under_lease(self, task: str, result_holder: "
    "SubagentResult | None = None) -> SubagentResult:\n"
)
_WRAPPER = (
    "    async def _aexecute(self, task: str, result_holder: "
    "SubagentResult | None = None) -> SubagentResult:\n"
    "        \"\"\"Execute under the application-wide subagent lifecycle envelope.\"\"\"\n"
    "        from deerflow.agents.middlewares.model_concurrency_middleware import (\n"
    "            async_subagent_lifecycle_lease,\n"
    "        )\n\n"
    "        async with async_subagent_lifecycle_lease():\n"
    "            return await self._aexecute_under_lease(task, result_holder)\n\n"
    + _LEASED_SIGNATURE
)

_EXECUTOR_COMPLETION_ANCHOR = (
    '            logger.info(f"[trace={self.trace_id}] Subagent '
    '{self.config.name} completed async execution")\n'
)
_EXECUTOR_FALLBACK_MARKER = (
    "DRF overlay: provider fallback messages are failed tasks"
)
_EXECUTOR_FALLBACK_GUARD = (
    "            # DRF overlay: provider fallback messages are failed tasks, not evidence.\n"
    "            # LLMErrorHandlingMiddleware deliberately returns an AIMessage so the\n"
    "            # graph can unwind cleanly; the executor must preserve its failure bit.\n"
    "            fallback_message = next(\n"
    "                (\n"
    "                    message\n"
    "                    for message in reversed((final_state or {}).get(\"messages\", []))\n"
    "                    if isinstance(message, AIMessage)\n"
    "                    and isinstance(message.additional_kwargs, dict)\n"
    "                    and message.additional_kwargs.get(\"deerflow_error_fallback\")\n"
    "                ),\n"
    "                None,\n"
    "            )\n"
    "            if fallback_message is not None:\n"
    "                fallback_meta = fallback_message.additional_kwargs\n"
    "                fallback_reason = str(fallback_meta.get(\"error_reason\") or \"provider\")\n"
    "                fallback_error = (\n"
    "                    \"LLM provider fallback inside subagent \"\n"
    "                    f\"(reason={fallback_reason})\"\n"
    "                )\n"
    "                result.try_set_terminal(\n"
    "                    SubagentStatus.FAILED,\n"
    "                    error=fallback_error,\n"
    "                    ai_messages=list(ai_messages),\n"
    "                    token_usage_records=collector.snapshot_records(),\n"
    "                )\n"
    "                logger.error(\n"
    "                    f\"[trace={self.trace_id}] Subagent {self.config.name} \"\n"
    "                    f\"terminated on {fallback_error}\"\n"
    "                )\n"
    "                return result\n\n"
)

_EXECUTOR_IMPORT_ORIGINAL = "import asyncio\n"
_EXECUTOR_IMPORT_PATCHED = "import asyncio\nimport json\n"
_EXECUTOR_HELPER_ANCHOR = "logger = logging.getLogger(__name__)\n"
_EXECUTOR_CONTROL_MARKER = "DRF overlay: classify typed blocked outcomes"
_EXECUTOR_CONTROL_HELPER = '''

# DRF overlay: classify typed blocked outcomes and all-denied evidence tasks.
def _drf_subagent_control_failure(messages, final_result):
    """Return a stable error only for machine-observable terminal failures."""
    text = str(final_result or "").strip()
    first_line = text.splitlines()[0].strip() if text else ""
    prefix = "SUBAGENT_OUTCOME: BLOCKED"
    if first_line.startswith(prefix):
        code = "unspecified"
        for token in first_line[len(prefix):].strip().split():
            if token.startswith("code="):
                code = token.split("=", 1)[1].strip() or "unspecified"
                break
        return f"Subagent reported blocked outcome (code={code})"

    attempts = denials = successes = 0
    for message in messages or []:
        if getattr(message, "type", None) != "tool":
            continue
        name = str(getattr(message, "name", "") or "")
        if name not in {"web_search", "web_fetch"}:
            continue
        attempts += 1
        content = getattr(message, "content", "")
        content = content if isinstance(content, str) else str(content)
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("error") == "research_budget_exhausted":
            denials += 1
            continue
        if isinstance(payload, dict):
            if name == "web_search":
                if isinstance(payload.get("results"), list) and payload["results"]:
                    successes += 1
            elif not payload.get("error"):
                successes += 1
        elif content.strip() and not content.lstrip().startswith("Error:"):
            successes += 1
    if attempts and denials and successes == 0:
        return "Subagent evidence tools exhausted before any usable result"
    return None
'''

_EXECUTOR_TERMINAL_ANCHOR = (
    "            result.try_set_terminal(\n"
    "                SubagentStatus.COMPLETED,\n"
    "                result=final_result,\n"
)
_EXECUTOR_CONTROL_GUARD = '''            control_failure = _drf_subagent_control_failure(
                (final_state or {}).get("messages", []), final_result
            )
            if control_failure is not None:
                result.try_set_terminal(
                    SubagentStatus.FAILED,
                    error=control_failure,
                    ai_messages=list(ai_messages),
                    token_usage_records=token_usage_records,
                )
                logger.error(
                    f"[trace={self.trace_id}] Subagent {self.config.name} "
                    f"terminated on {control_failure}"
                )
                return result

'''


def _updated_source(target: Path, original: str, patched: str, marker: str) -> tuple[str, bool]:
    source = target.read_text(encoding="utf-8")
    if marker in source:
        return source, False
    if source.count(original) != 1:
        raise RuntimeError(
            f"subagent overlay context drifted; refusing an unsafe edit: {target}"
        )
    return source.replace(original, patched, 1), True


def _atomic_write(target: Path, source: str) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(source, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Return ``applied``, ``already_applied``, or ``missing``."""
    root = Path(deerflow_root)
    targets = {
        "client": root / CLIENT_PATH,
        "task": root / TASK_TOOL_PATH,
        "executor": root / EXECUTOR_PATH,
    }
    if not all(target.is_file() for target in targets.values()):
        return "missing"

    updates: dict[Path, str] = {}

    client_source = targets["client"].read_text(encoding="utf-8")
    client_changed = False
    if "task subagents must receive the exact per-client config" not in client_source:
        if client_source.count(_CLIENT_CONTEXT_ORIGINAL) != 1:
            raise RuntimeError(
                "subagent overlay context drifted; refusing an unsafe edit: "
                f"{targets['client']}"
            )
        client_source = client_source.replace(
            _CLIENT_CONTEXT_ORIGINAL, _CLIENT_CONTEXT_PATCHED, 1
        )
        client_changed = True
    if _CLIENT_USAGE_MARKER not in client_source and _CLIENT_STREAM_USAGE_MARKER not in client_source:
        for original, patched, what in (
            (_CLIENT_USAGE_DECL_ORIGINAL, _CLIENT_USAGE_DECL_PATCHED, "declaration"),
            (_CLIENT_USAGE_BODY_ORIGINAL, _CLIENT_USAGE_BODY_PATCHED, "body"),
        ):
            if client_source.count(original) != 1:
                raise RuntimeError(
                    f"client usage overlay {what} context drifted; refusing "
                    f"an unsafe edit: {targets['client']}"
                )
            client_source = client_source.replace(original, patched, 1)
        client_changed = True
    stream_source = _patch_client_stream_usage(client_source, targets["client"])
    client_changed = client_changed or stream_source != client_source
    client_source = stream_source
    if client_changed:
        updates[targets["client"]] = client_source

    task_source, changed = _updated_source(
        targets["task"],
        _TASK_MODEL_ORIGINAL,
        _TASK_MODEL_PATCHED,
        "embedded clients carry the active model under",
    )
    if changed:
        updates[targets["task"]] = task_source

    executor_source = targets["executor"].read_text(encoding="utf-8")
    executor_changed = False
    if _EXECUTOR_CONTROL_MARKER not in executor_source:
        if "import json\n" not in executor_source:
            if executor_source.count(_EXECUTOR_IMPORT_ORIGINAL) != 1:
                raise RuntimeError(
                    "subagent control overlay import context drifted; refusing "
                    f"an unsafe edit: {targets['executor']}"
                )
            executor_source = executor_source.replace(
                _EXECUTOR_IMPORT_ORIGINAL, _EXECUTOR_IMPORT_PATCHED, 1
            )
        if executor_source.count(_EXECUTOR_HELPER_ANCHOR) != 1:
            raise RuntimeError(
                "subagent control overlay helper context drifted; refusing "
                f"an unsafe edit: {targets['executor']}"
            )
        executor_source = executor_source.replace(
            _EXECUTOR_HELPER_ANCHOR,
            _EXECUTOR_HELPER_ANCHOR + _EXECUTOR_CONTROL_HELPER,
            1,
        )
        executor_changed = True
    if "async def _aexecute_under_lease(" not in executor_source:
        if executor_source.count(_ORIGINAL_SIGNATURE) != 1:
            raise RuntimeError(
                "subagent overlay context drifted; refusing an unsafe edit: "
                f"{targets['executor']}"
            )
        executor_source = executor_source.replace(
            _ORIGINAL_SIGNATURE, _WRAPPER, 1
        )
        executor_changed = True
    if _EXECUTOR_FALLBACK_MARKER not in executor_source:
        if executor_source.count(_EXECUTOR_COMPLETION_ANCHOR) != 1:
            raise RuntimeError(
                "subagent fallback overlay context drifted; refusing an unsafe edit: "
                f"{targets['executor']}"
            )
        executor_source = executor_source.replace(
            _EXECUTOR_COMPLETION_ANCHOR,
            _EXECUTOR_FALLBACK_GUARD + _EXECUTOR_COMPLETION_ANCHOR,
            1,
        )
        executor_changed = True
    if "control_failure = _drf_subagent_control_failure(" not in executor_source:
        if executor_source.count(_EXECUTOR_TERMINAL_ANCHOR) != 1:
            raise RuntimeError(
                "subagent control overlay terminal context drifted; refusing "
                f"an unsafe edit: {targets['executor']}"
            )
        executor_source = executor_source.replace(
            _EXECUTOR_TERMINAL_ANCHOR,
            _EXECUTOR_CONTROL_GUARD + _EXECUTOR_TERMINAL_ANCHOR,
            1,
        )
        executor_changed = True
    if executor_changed:
        updates[targets["executor"]] = executor_source

    for target, source in updates.items():
        _atomic_write(target, source)
    return "applied" if updates else "already_applied"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_subagent_overlays.py <deer-flow-root>")
    print(apply(sys.argv[1]))
