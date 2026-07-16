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

    client_source, changed = _updated_source(
        targets["client"],
        _CLIENT_CONTEXT_ORIGINAL,
        _CLIENT_CONTEXT_PATCHED,
        "task subagents must receive the exact per-client config",
    )
    if changed:
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
