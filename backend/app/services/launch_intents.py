"""Durable, fail-closed admission for a client-owned pipeline launch intent.

An intent grants exactly one dispatch opportunity. It is never expired or
automatically reclaimed: after a crash, absence of a successful reply is not
evidence that the background work did not begin. Pipeline state remains the
execution authority; this small ledger retains identity even after deletion.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_STATES = {"admitted", "dispatching", "dispatched", "failed", "abandoned"}


class LaunchIntentConflict(RuntimeError):
    """An existing intent identifies a different canonical launch request."""

    def __init__(self, pipeline_id: str):
        super().__init__("Launch intent is already associated with a different request")
        self.pipeline_id = pipeline_id


class LaunchIntentStorageError(RuntimeError):
    """Admission could not be read or persisted; callers must not fall back."""

    def __init__(self, pipeline_id: str | None = None):
        super().__init__("Launch-intent storage is unavailable; retry the same intent")
        self.pipeline_id = pipeline_id


def validate_intent_key(key: Any) -> str:
    """Keep keys opaque, bounded and suitable for a header or local storage."""
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise ValueError("Launch intent must contain 16-128 URL-safe letters, digits, underscores or hyphens")
    return key


def _key_hash(key: str) -> str:
    return hashlib.sha256(validate_intent_key(key).encode("utf-8")).hexdigest()


def _request_hash(request: dict[str, Any]) -> str:
    if not isinstance(request, dict):
        raise ValueError("Canonical launch request must be an object")
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LaunchIntentStore:
    """One SQLite admission database per pipeline data root, outside run dirs.

    Only reserve() and transition() write. lookup() opens an existing database
    read-only and does not initialize directories or a missing database. Each
    call owns its connection, allowing threads and separate backend processes
    to contend on the same UNIQUE key under BEGIN IMMEDIATE.
    """

    def __init__(self, pipeline_root: str):
        self.path = Path(pipeline_root).absolute() / "launch_intents.sqlite3"

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        else:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        if write:
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.get("schema_version") != 1 or result.get("launch_status") not in _STATES:
            raise ValueError("Unsupported launch-intent record")
        state = json.loads(result.pop("initial_state_json"))
        abandoned = result["launch_status"] == "abandoned"
        if abandoned:
            if result["pipeline_id"] is not None or state != {}:
                raise ValueError("Invalid abandoned launch-intent tombstone")
        elif not isinstance(state, dict) or not result["pipeline_id"] or state.get("pipeline_id") != result["pipeline_id"]:
            raise ValueError("Invalid launch-intent state snapshot")
        result["initial_state"] = state
        return result

    @staticmethod
    def _ensure_table(connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS launch_intents (
                key_hash TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                pipeline_id TEXT UNIQUE,
                initial_state_json TEXT NOT NULL,
                launch_status TEXT NOT NULL CHECK (launch_status IN ('admitted','dispatching','dispatched','failed','abandoned')),
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            )
        """)

    def lookup(self, key: str, canonical_request: dict[str, Any] | None = None) -> dict[str, Any] | None:
        hashed_key = _key_hash(key)
        request_hash = _request_hash(canonical_request) if canonical_request is not None else None
        if not self.path.exists():
            return None
        connection = None
        try:
            connection = self._connect(write=False)
            # The first reserve creates the database file before committing its
            # schema. A concurrent reader may observe that brief empty-file
            # window. Wait boundedly for schema publication, without treating a
            # persistently missing/corrupt ledger table as permission to launch.
            schema_deadline = time.monotonic() + 1.0
            while True:
                try:
                    row = connection.execute(
                        "SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,),
                    ).fetchone()
                    break
                except sqlite3.OperationalError as exc:
                    if "no such table: launch_intents" not in str(exc) or time.monotonic() >= schema_deadline:
                        raise
                    time.sleep(0.01)
            if row is None:
                return None
            record = self._decode(row)
            if (record["launch_status"] != "abandoned" and request_hash is not None
                    and record["request_hash"] != request_hash):
                raise LaunchIntentConflict(record["pipeline_id"])
            return record
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise LaunchIntentStorageError() from exc
        finally:
            if connection is not None:
                connection.close()

    def reserve(
        self, key: str, canonical_request: dict[str, Any], initial_state: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Commit the immutable identity/snapshot before any dispatch side effect."""
        hashed_key, request_hash = _key_hash(key), _request_hash(canonical_request)
        snapshot = json.dumps(initial_state, ensure_ascii=False, sort_keys=True, allow_nan=False)
        pipeline_id = initial_state["pipeline_id"]
        connection = None
        try:
            connection = self._connect(write=True)
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_table(connection)
            existing = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            if existing is not None:
                record = self._decode(existing)
                if record["launch_status"] != "abandoned" and record["request_hash"] != request_hash:
                    raise LaunchIntentConflict(record["pipeline_id"])
                connection.commit()
                return record, False
            now = _now()
            connection.execute(
                """INSERT INTO launch_intents
                   (key_hash, request_hash, pipeline_id, initial_state_json, launch_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'admitted', ?, ?)""",
                (hashed_key, request_hash, pipeline_id, snapshot, now, now),
            )
            row = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            record = self._decode(row)
            connection.commit()
            return record, True
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise LaunchIntentStorageError(pipeline_id) from exc
        finally:
            if connection is not None:
                # Closing rolls back any failed/uncommitted admission. A thread
                # is never started until this method has returned successfully.
                connection.close()

    def abandon(self, key: str) -> tuple[dict[str, Any], bool]:
        """Atomically prohibit a still-unadmitted key from ever dispatching.

        This is an explicit client action, serialized with reserve on the same
        unique key. If admission already won, return its identity unchanged;
        abandonment does not cancel, delete, or mutate an admitted pipeline.
        """
        hashed_key = _key_hash(key)
        connection = None
        try:
            connection = self._connect(write=True)
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_table(connection)
            row = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            created = row is None
            if created:
                now = _now()
                connection.execute(
                    """INSERT INTO launch_intents
                       (key_hash, request_hash, pipeline_id, initial_state_json, launch_status, created_at, updated_at)
                       VALUES (?, '', NULL, '{}', 'abandoned', ?, ?)""",
                    (hashed_key, now, now),
                )
                row = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            record = self._decode(row)
            connection.commit()
            return record, created
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise LaunchIntentStorageError() from exc
        finally:
            if connection is not None:
                connection.close()

    def transition(self, key: str, state: str, *, error_code: str | None = None) -> dict[str, Any]:
        """Advance the one winning dispatch; no transition grants a retry."""
        hashed_key = _key_hash(key)
        expected = {
            "dispatching": {"admitted"},
            "dispatched": {"dispatching"},
            "failed": {"admitted", "dispatching"},
        }
        if state not in expected:
            raise ValueError("Invalid launch-intent transition")
        connection = None
        pipeline_id = None
        try:
            connection = self._connect(write=True)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            if row is None:
                raise ValueError("Missing launch-intent reservation")
            record = self._decode(row)
            pipeline_id = record["pipeline_id"]
            if record["launch_status"] not in expected[state]:
                raise ValueError("Launch-intent dispatch cannot be reclaimed")
            connection.execute(
                "UPDATE launch_intents SET launch_status = ?, error_code = ?, updated_at = ? WHERE key_hash = ?",
                (state, error_code, _now(), hashed_key),
            )
            row = connection.execute("SELECT * FROM launch_intents WHERE key_hash = ?", (hashed_key,)).fetchone()
            updated = self._decode(row)
            connection.commit()
            return updated
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise LaunchIntentStorageError(pipeline_id) from exc
        finally:
            if connection is not None:
                connection.close()
