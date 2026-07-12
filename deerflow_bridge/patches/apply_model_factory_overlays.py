"""Apply narrow, idempotent overlays to DeerFlow's model factory.

``context_window_tokens`` is local budgeting metadata consumed by the research
bridge.  DeerFlow's permissive ``ModelConfig`` otherwise forwards every unknown
key to the provider constructor; LangChain then places this one in
``model_kwargs`` and sends it to OpenAI-compatible APIs, which reject it.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


MODEL_FACTORY_PATH = Path(
    "backend/packages/harness/deerflow/models/factory.py")

_ORIGINAL_EXCLUDE_TAIL = '''            "supports_vision",
        },
    )
'''

_METADATA_SAFE_EXCLUDE_TAIL = '''            "supports_vision",
            # Research-bridge budgeting metadata; never a provider/API kwarg.
            "context_window_tokens",
        },
    )
'''


def apply(deerflow_root: str | os.PathLike[str]) -> str:
    """Return ``applied``, ``already_applied``, or ``missing``."""
    target = Path(deerflow_root) / MODEL_FACTORY_PATH
    if not target.is_file():
        return "missing"
    source = target.read_text(encoding="utf-8")
    if _METADATA_SAFE_EXCLUDE_TAIL in source:
        return "already_applied"
    if source.count(_ORIGINAL_EXCLUDE_TAIL) != 1:
        raise RuntimeError(
            f"model-factory overlay context drifted; refusing an unsafe edit: {target}"
        )
    updated = source.replace(
        _ORIGINAL_EXCLUDE_TAIL, _METADATA_SAFE_EXCLUDE_TAIL, 1)
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
        raise SystemExit(
            "usage: apply_model_factory_overlays.py <deer-flow-root>")
    status = apply(sys.argv[1])
    print(status)
    if status == "missing":
        raise SystemExit(2)
