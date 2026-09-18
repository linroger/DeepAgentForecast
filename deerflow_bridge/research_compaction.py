"""Durable producer receipts for research conversation compaction.

Only a successful, committed receipt permits the middleware to remove messages.
The original LangChain message dictionaries remain in this append-only archive;
a summary is derived evidence and never a fetched-source or search-result receipt.
This module deliberately uses only the standard library so the bridge and the
assembled DeerFlow harness share the same persistence and validation contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "research-compaction/v1"
PRODUCER = "deerflow-summarization"
EVIDENCE_KIND = "derived_summary"
METADATA_KEY = "drf_compaction"
# Exceeding these bounds stops compaction; original evidence is never truncated.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SUMMARY_BYTES = 4 * 1024 * 1024

_REASONS = frozenset({
    "compaction_failed", "archive_unavailable", "archive_write_failed",
    "archive_conflict", "invalid_compaction", "archive_too_large",
    "summary_failed", "summary_empty", "summary_invalid", "summary_too_large",
    "summary_unavailable", "summary_input_empty", "summary_serialization_failed",
    "missing_thread_id", "provider_error", "invalid_summary",
    "summary_model_failed", "empty_summary_input", "summary_prompt_failed",
    "checkpoint_persistence_failed", "checkpoint_unavailable",
})


class ResearchCompactionError(RuntimeError):
    """Safe control-plane stop that preserves the previous checkpoint.

    Provider text and filesystem details are intentionally absent from the
    public message. Callers use ``reason`` as a stable machine-readable code.
    """

    code = "research_compaction_failed"

    def __init__(self, reason: str, thread_id: str = "") -> None:
        self.reason = (
            reason if isinstance(reason, str) and reason in _REASONS
            else "compaction_failed"
        )
        self.thread_id = thread_id
        super().__init__(f"{self.code}: {self.reason}")


_STOP_LOCK = threading.Lock()
_COMPACTION_STOP: ResearchCompactionError | None = None


def stop_after_compaction_failure(error: ResearchCompactionError) -> None:
    """Latch the first compaction failure across concurrent lanes in this run."""
    global _COMPACTION_STOP
    if not isinstance(error, ResearchCompactionError):
        raise TypeError("a typed research compaction failure is required")
    with _STOP_LOCK:
        if _COMPACTION_STOP is None:
            _COMPACTION_STOP = ResearchCompactionError(error.reason, error.thread_id)


def get_compaction_stop() -> ResearchCompactionError | None:
    """Return a safe snapshot of the run's terminal compaction condition."""
    with _STOP_LOCK:
        if _COMPACTION_STOP is None:
            return None
        return ResearchCompactionError(
            _COMPACTION_STOP.reason, _COMPACTION_STOP.thread_id,
        )


def raise_if_compaction_stopped() -> None:
    """Reject another provider operation once evidence preservation has failed."""
    error = get_compaction_stop()
    if error is not None:
        raise error


def reset_compaction_stop() -> None:
    """Reset only at a new top-level research run, never between passes/lanes."""
    global _COMPACTION_STOP
    with _STOP_LOCK:
        _COMPACTION_STOP = None


def _db_path(archive_path: str | None = None) -> Path:
    raw = archive_path if archive_path is not None else (
        os.environ.get("RESEARCH_COMPACTION_DB", "").strip()
        or os.environ.get("RESEARCH_BUDGET_DB", "").strip()
    )
    if not isinstance(raw, str):
        raise ResearchCompactionError("archive_unavailable")
    raw = raw.strip()
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        raise ResearchCompactionError("archive_unavailable")
    return Path(raw).expanduser().resolve()


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_identity(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= 1024
        and value == value.strip() and "\x00" not in value
    )


def _envelope(
    thread_id: str, message_id: str, content: str, source_json: str,
    source_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "producer": PRODUCER,
        "evidence_kind": EVIDENCE_KIND,
        "thread_id": thread_id,
        "message_id": message_id,
        "content_sha256": _sha256(content),
        "source_messages_sha256": _sha256(source_json),
        "source_message_count": source_count,
    }


def record_compaction(
    *, thread_id: str, message_id: str, content: str,
    source_messages: list[dict], archive_path: str | None = None,
) -> dict[str, Any]:
    """Commit the exact removed messages and return their summary envelope.

    Identical retries with one message ID are idempotent. Any changed content,
    source lineage, or thread identity under that ID fails closed. SQLite's
    FULL synchronous commit completes before the caller can delete messages.
    A native harness may supply its trusted runtime archive path explicitly;
    that private local path is never included in the summary envelope.
    """
    if (
        not _valid_identity(thread_id) or not _valid_identity(message_id)
        or not isinstance(content, str) or not content.strip()
        or not isinstance(source_messages, list) or not source_messages
        or not all(isinstance(message, dict) for message in source_messages)
    ):
        raise ResearchCompactionError("invalid_compaction", thread_id)
    try:
        source_json = _canonical(source_messages)
        if json.loads(source_json) != source_messages:
            raise ValueError("original messages are not losslessly JSON serializable")
        source_bytes = source_json.encode("utf-8")
        content_bytes = content.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ResearchCompactionError("invalid_compaction", thread_id) from None
    if len(source_bytes) > MAX_ARCHIVE_BYTES:
        raise ResearchCompactionError("archive_too_large", thread_id)
    if len(content_bytes) > MAX_SUMMARY_BYTES:
        raise ResearchCompactionError("summary_too_large", thread_id)
    envelope = _envelope(
        thread_id, message_id, content, source_json, len(source_messages),
    )
    envelope_json = _canonical(envelope)
    try:
        path = _db_path(archive_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path), timeout=5.0)) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS compaction_receipts (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_messages_json TEXT NOT NULL,
                    envelope_json TEXT NOT NULL
                )""")
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT thread_id, content, source_messages_json, envelope_json "
                    "FROM compaction_receipts WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                expected = (thread_id, content, source_json, envelope_json)
                if existing is not None:
                    if existing != expected:
                        raise ResearchCompactionError("archive_conflict", thread_id)
                else:
                    conn.execute(
                        "INSERT INTO compaction_receipts "
                        "(message_id, thread_id, content, source_messages_json, "
                        "envelope_json) VALUES (?, ?, ?, ?, ?)",
                        (message_id, *expected),
                    )
        return envelope
    except ResearchCompactionError as exc:
        raise ResearchCompactionError(exc.reason, thread_id) from None
    except (OSError, sqlite3.Error, ValueError):
        raise ResearchCompactionError("archive_write_failed", thread_id) from None


def validate_compaction(message: dict, required_thread_id: str) -> bool:
    """Admit only an exact producer-recorded summary for the requested thread.

    Metadata and self-computed hashes alone provide no authority. Validation
    reopens the archive read-only and compares its committed row, including the
    original-message digest, so it works after process exit/checkpoint reload.
    Missing, corrupt, or unavailable archives never turn human text into evidence.
    """
    if (
        not isinstance(message, dict)
        or message.get("type") not in ("human", "user")
        or not _valid_identity(required_thread_id)
        or not _valid_identity(message.get("id"))
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
    ):
        return False
    kwargs = message.get("additional_kwargs")
    envelope = kwargs.get(METADATA_KEY) if isinstance(kwargs, dict) else None
    if not isinstance(envelope, dict):
        return False
    count = envelope.get("source_message_count")
    if (
        type(count) is not int or count < 1
        or envelope.get("thread_id") != required_thread_id
        or envelope.get("message_id") != message["id"]
        or envelope.get("schema_version") != SCHEMA_VERSION
        or envelope.get("producer") != PRODUCER
        or envelope.get("evidence_kind") != EVIDENCE_KIND
    ):
        return False
    try:
        if len(message["content"].encode("utf-8")) > MAX_SUMMARY_BYTES:
            return False
        path = _db_path()
        with closing(sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, timeout=5.0,
        )) as conn:
            row = conn.execute(
                "SELECT thread_id, content, source_messages_json, envelope_json "
                "FROM compaction_receipts WHERE message_id = ?",
                (message["id"],),
            ).fetchone()
        if row is None or row[0] != required_thread_id or row[1] != message["content"]:
            return False
        if len(row[2].encode("utf-8")) > MAX_ARCHIVE_BYTES:
            return False
        originals = json.loads(row[2])
        if (
            not isinstance(originals, list) or len(originals) != count
            or not all(isinstance(original, dict) for original in originals)
        ):
            return False
        expected = _envelope(
            required_thread_id, message["id"], message["content"], row[2], count,
        )
        return envelope == expected and row[3] == _canonical(expected)
    except (
        ResearchCompactionError, OSError, sqlite3.Error, TypeError, ValueError,
        UnicodeError, RecursionError,
    ):
        return False


# The tracked bridge can be imported as a namespace package, while deployed
# scripts import this helper by its bare name. Both must share the exception
# class and stop latch; otherwise an import fallback can bypass the stop.
if __name__ in {"research_compaction", "deerflow_bridge.research_compaction"}:
    _canonical_module = sys.modules.setdefault("research_compaction", sys.modules[__name__])
    sys.modules["deerflow_bridge.research_compaction"] = _canonical_module
