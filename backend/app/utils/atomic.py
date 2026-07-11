"""Atomic file-write helpers.

A watchdog ``SIGKILL`` mid-write, or a polling reader that opens a file while a
writer is still flushing, can otherwise observe a truncated / half-written
artifact and corrupt the cross-stage contract. Every artifact and state writer
should route through these helpers: write to a sibling temp file, ``fsync``, then
``os.replace`` (atomic rename on the same filesystem).

EXECPLAN2: F-0-4, F-6-13, F-7-6, F-8-1 (atomic-write-everywhere theme).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def write_text_atomic(
    path: str, text: str, *, encoding: str = "utf-8", fsync: bool = True
) -> None:
    """Atomically write *text* to *path* (create parent dir if needed).

    The temp-file + ``os.replace`` rename is always atomic, so a reader never
    observes a half-written file regardless of *fsync*. ``fsync`` only controls
    whether the bytes are forced to the platter before the rename:

    - ``fsync=True`` (default) survives a hard power loss / kernel panic — keep
      it for durable artifacts (forecast.json, dossiers, terminal state).
    - ``fsync=False`` skips the (slow) flush for high-frequency status/progress
      writes that are overwritten seconds later anyway; the rename is still
      atomic, so concurrent pollers stay safe. (ATOMIC-1)
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(
    path: str,
    obj: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    fsync: bool = True,
    allow_nan: bool = True,
) -> None:
    """Atomically serialize *obj* to JSON at *path*.

    *fsync* is forwarded to :func:`write_text_atomic`; pass ``fsync=False`` at
    high-frequency status/progress sites only. (ATOMIC-1)
    """
    write_text_atomic(
        path,
        json.dumps(
            obj,
            ensure_ascii=ensure_ascii,
            indent=indent,
            default=str,
            allow_nan=allow_nan,
        ),
        fsync=fsync,
    )
