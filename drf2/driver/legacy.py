"""Bridge to the legacy backend (import-only reuse, REDESIGN.md §3).

drf2 lives at the repo root while the legacy capability core is the ``app`` package
under ``backend/``. This helper makes ``import app...`` work regardless of where the
driver is launched from, so we can reuse audited modules (atomic writes, ensemble
math, forecast conviction scorecard) instead of duplicating them.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_BACKEND_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))


def ensure_backend_importable() -> None:
    """幂等：若 ``app`` 包不可导入，则把 ``backend/`` 加进 sys.path。"""
    if importlib.util.find_spec("app") is not None:
        return
    if os.path.isdir(_BACKEND_DIR) and _BACKEND_DIR not in sys.path:
        sys.path.insert(0, _BACKEND_DIR)
    if importlib.util.find_spec("app") is None:  # pragma: no cover — repo layout broken
        raise ImportError(f"legacy backend package 'app' not importable (looked in {_BACKEND_DIR})")
