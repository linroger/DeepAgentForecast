"""Apply small, tracked overlays to DeerFlow's lead-agent factory.

The upstream harness is installed under the gitignored ``deer-flow/`` tree, so a
direct edit there disappears on a clean clone.  Keep narrowly scoped, idempotent
source transformations here and invoke them from both ``setup.sh`` and the runtime
bridge drift guard.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


LEAD_AGENT_PATH = Path(
    "backend/packages/harness/deerflow/agents/lead_agent/agent.py"
)

_OLD_TRIM_BLOCK = '''    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize
'''

_NEW_TRIM_BLOCK = '''    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
        # ``None`` is an explicit contract: summarize the complete discarded
        # segment. Omitting this kwarg activates LangChain's 4K default and
        # silently drops the beginning of long research histories.
        "trim_tokens_to_summarize": config.trim_tokens_to_summarize,
    }
'''


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Apply the overlay and return ``applied``, ``already_applied``, or ``missing``.

    An unexpected upstream shape raises instead of pretending the safety contract
    landed.  Callers decide whether that should be fatal (tests) or degrade-safe
    (runtime drift guard).
    """

    target = Path(deerflow_root) / LEAD_AGENT_PATH
    if not target.is_file():
        return "missing"
    source = target.read_text(encoding="utf-8")
    if _NEW_TRIM_BLOCK in source:
        return "already_applied"
    if _OLD_TRIM_BLOCK not in source:
        raise RuntimeError(
            f"lead-agent overlay context drifted; refusing an unsafe edit: {target}"
        )
    updated = source.replace(_OLD_TRIM_BLOCK, _NEW_TRIM_BLOCK, 1)
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
        raise SystemExit("usage: apply_lead_agent_overlays.py <deer-flow-root>")
    status = apply(sys.argv[1])
    print(status)
    if status == "missing":
        raise SystemExit(2)
