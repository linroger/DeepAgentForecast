"""Apply narrow, idempotent overlays to DeerFlow's subagent executor.

The vendor seed evolves independently of the active gitignored runtime. Never
copy a complete executor across versions: doing so can erase tracing, session,
and callback propagation. This transformer changes only the async lifecycle
entrypoint while preserving every surrounding upstream byte.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


EXECUTOR_PATH = Path(
    "backend/packages/harness/deerflow/subagents/executor.py")

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


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Return ``applied``, ``already_applied``, or ``missing``."""
    target = Path(deerflow_root) / EXECUTOR_PATH
    if not target.is_file():
        return "missing"
    source = target.read_text(encoding="utf-8")
    if "async def _aexecute_under_lease(" in source:
        return "already_applied"
    if source.count(_ORIGINAL_SIGNATURE) != 1:
        raise RuntimeError(
            f"subagent overlay context drifted; refusing an unsafe edit: {target}"
        )
    updated = source.replace(_ORIGINAL_SIGNATURE, _WRAPPER, 1)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return "applied"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_subagent_overlays.py <deer-flow-root>")
    print(apply(sys.argv[1]))
