"""Standalone durable storage for one semantic research run/lane.

All identity fields are bound, except the explicit top-level physical-attempt
fields below. Callers supply semantic run, lane, question, model, language and
policy identities; this module does not infer or truncate them. ``load_task``
reserves an immutable input identity even before a result exists. It returns the
original result dictionary only after a complete, verified task receipt commits.

Blobs are UTF-8, content addressed, fsynced and atomically published before their
SQLite receipts. A crash may leave an orphan blob/receipt, never a completed task
without durable evidence. Existing corrupt stores are rejected, never rebuilt.
Every operation opens and closes its own SQLite connection (WAL, FULL sync).
Initialization builds in an unpublished sibling directory before an atomic
directory rename. Only that staging directory can be rebuilt after a crash;
published workspace roots always pass through the normal integrity gates.

``execution_lock`` is a nonblocking POSIX process lock. Hold it around admission,
execution and completion; defer its release until any outstanding futures drain
when returning early. Storage transactions alone cannot prevent duplicate
provider work. All participants must cooperate and use the same local root.
Checksums detect damage, not malicious rewriting of the entire trusted store.
"""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import Future
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import types
import tempfile
import threading
from typing import Iterable


SCHEMA_VERSION = "research-workspace/v1"
_PHYSICAL_FIELDS = frozenset({
    "attempt", "attempt_id", "process_attempt", "process_attempt_id",
    "physical_attempt", "physical_attempt_id", "pid", "process_id",
})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REF_KEYS = frozenset({"id", "sha256", "bytes", "path"})
_LEASE_GUARD = threading.Lock()
_ACTIVE_LEASES = {}
_SEARCH_CHARS = 1024
_SEARCH_OVERLAP = 128
_SEARCH_SKIP_KINDS = frozenset({
    "task_receipt", "assistant_delta", "assistant_chunk", "native_pass_message",
    "unverified_partial_message",
})


def _search_terms(text):
    """Literal Unicode terms, with CJK bigrams for scripts without spaces."""
    terms = re.findall(r"[^\W_]+", text.casefold())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))


class ResearchWorkspaceError(RuntimeError):
    """Persistence/identity failure: stop execution rather than silently retry."""


class ResearchArtifactNotFoundError(ResearchWorkspaceError):
    """A caller-supplied SHA has no row in this workspace's artifact registry.

    Only public ID lookup raises this subtype. Missing receipts while reading a
    supplied reference or validating persisted tasks/events remain integrity
    failures, as do missing blob files, damaged records and identity conflicts.
    """


# Bare deployment and package imports must share control-error identities, even
# when two cold imports execute under different Python module locks.
_error_candidate = types.ModuleType("_drf_workspace_errors_v1")
_error_candidate.ResearchWorkspaceError = ResearchWorkspaceError
_error_candidate.ResearchArtifactNotFoundError = ResearchArtifactNotFoundError
_error_types = sys.modules.setdefault(_error_candidate.__name__, _error_candidate)
ResearchWorkspaceError = _error_types.ResearchWorkspaceError
ResearchArtifactNotFoundError = _error_types.ResearchArtifactNotFoundError


class ExecutionLease:
    """Thread-neutral execution ownership, retained after a bounded return.

    Deferral must be registered before leaving ``execution_lock``. Its context
    exit never waits for futures. Future completion callbacks close the OS file
    descriptor exactly once after both context exit and all deferred futures.
    A permanently wedged future deliberately retains ownership until process
    exit. Storage methods use independent connections and remain available.
    """

    def __init__(self, root, fd):
        self._root = root
        self._fd = fd
        self._guard = threading.Lock()
        self._pending = set()
        self._exited = False

    def defer_release_until(self, futures: Iterable[Future]) -> None:
        pending = set(futures)
        if not all(isinstance(future, Future) for future in pending):
            raise TypeError("lease deferral requires concurrent.futures.Future values")
        with self._guard:
            if self._exited:
                raise ResearchWorkspaceError("execution lease context is closed")
            newly_registered = pending - self._pending
            self._pending.update(newly_registered)
        # A callback may run synchronously for a finished future, so register it
        # outside the non-reentrant guard. The pending set already owns all of
        # these futures if context exit races callback registration.
        for future in newly_registered:
            future.add_done_callback(self._future_done)

    def _future_done(self, future):
        with self._guard:
            self._pending.discard(future)
            self._release_if_ready()

    def _context_exited(self):
        with self._guard:
            self._exited = True
            self._release_if_ready()

    def _release_if_ready(self):
        if not self._exited or self._pending or self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.close(fd)
        finally:
            with _LEASE_GUARD:
                if _ACTIVE_LEASES.get(self._root) is self:
                    del _ACTIVE_LEASES[self._root]


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ResearchWorkspaceError(f"invalid {label}")
    return value


def _json(value):
    """Require lossless JSON; tuples, non-string keys and nonfinite floats fail."""
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for entry in item:
                check(entry)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for entry in item.values():
                check(entry)
            return
        raise ResearchWorkspaceError("value is not lossless JSON")

    try:
        check(value)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ResearchWorkspaceError("value is not lossless JSON") from exc


def _object(value, label):
    if type(value) is not dict:
        raise ResearchWorkspaceError(f"invalid {label}: expected dictionary")
    return _json(value)


def _decode(encoded):
    try:
        value = json.loads(encoded)
        if _json(value) != encoded:
            raise ResearchWorkspaceError("noncanonical or corrupt record")
        return value
    except (TypeError, ValueError) as exc:
        raise ResearchWorkspaceError("corrupt JSON record") from exc


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ResearchWorkspace:
    """One local run workspace; arbitrary caller IDs are stored only as data."""

    def __init__(self, root: Path, identity: dict):
        _object(identity, "identity")
        semantic = {key: value for key, value in identity.items() if key not in _PHYSICAL_FIELDS}
        if not semantic:
            raise ResearchWorkspaceError("semantic identity is required")
        self._identity_json = _json(semantic)
        self._identity_sha = _sha(self._identity_json)
        self.root = Path(root).expanduser().resolve()
        self._manifest = _json({
            "schema_version": SCHEMA_VERSION, "identity": semantic,
            "sha256": self._identity_sha,
        })
        try:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            # A known wrong identity fails without opening any writable file.
            if (self.root / "identity.json").exists():
                self._check_manifest()
            # The lock lives beside the root, so its inode remains stable while
            # the complete staging directory is renamed into place.
            bootstrap = ".research-init-" + _sha(str(self.root))
            with self._file_lock(self.root.parent / (bootstrap + ".lock")):
                if self.root.exists() and any(self.root.iterdir()):
                    self._check_manifest()
                    if not (self.root / "workspace.sqlite3").is_file():
                        raise ResearchWorkspaceError("workspace database missing")
                else:
                    self._bootstrap(self.root.parent / (bootstrap + ".staging"))
            # Validate every committed receipt before callers can start execution.
            self.snapshot()
            self._ensure_event_kind_index()
            self._ensure_search_index()
        except (OSError, sqlite3.Error) as exc:
            raise ResearchWorkspaceError("workspace initialization failed") from exc

    @property
    def identity(self) -> dict:
        """Return a detached copy of the bound semantic identity."""
        return _decode(self._identity_json)

    def _safe_file(self, path):
        if path.parent != self.root:
            if path.parent != self.root / "blobs" or path.parent.is_symlink():
                raise ResearchWorkspaceError("unsafe workspace path")
        if path.is_symlink():
            raise ResearchWorkspaceError("unsafe workspace file")
        return path

    def _read_bytes(self, path):
        try:
            path = self._safe_file(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ResearchWorkspaceError("workspace file is not regular")
                return stream.read()
        except OSError as exc:
            raise ResearchWorkspaceError("workspace file unavailable") from exc

    def _check_manifest(self):
        if self._read_bytes(self.root / "identity.json") != self._manifest.encode("utf-8"):
            raise ResearchWorkspaceError("workspace identity mismatch or corrupt manifest")

    @contextmanager
    def _file_lock(self, path):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ResearchWorkspaceError("invalid initialization lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            # Closing also releases the OS lock after exceptions/process death.
            os.close(fd)

    def _bootstrap(self, staging):
        """Resume only an unpublished bootstrap; never repair a final root.

        The staging name derives from the physical root, not caller IDs. Its
        immutable identity survives retries. No caller can write task/evidence
        records here: publication and full validation precede constructor return.
        A crash anywhere in preparation can therefore discard only the staging
        SQLite files. Unknown files, symlinks, or actual blob evidence stop this
        recovery path instead of being deleted.
        """
        destination = self.root
        if staging.is_symlink():
            raise ResearchWorkspaceError("unsafe initialization staging directory")
        staging.mkdir(exist_ok=True)
        self.root = staging
        try:
            entries = list(staging.iterdir())
            database_files = {"workspace.sqlite3", "workspace.sqlite3-wal", "workspace.sqlite3-shm"}
            has_identity = (staging / "identity.json").exists()
            if has_identity:
                self._check_manifest()
            for entry in entries:
                if entry.is_symlink():
                    raise ResearchWorkspaceError("unsafe initialization staging file")
                if entry.name == "blobs" and has_identity:
                    if not entry.is_dir() or any(entry.iterdir()):
                        raise ResearchWorkspaceError("unexpected evidence in initialization staging")
                elif not entry.is_file() or not (
                    entry.name.startswith(".pending-")
                    or has_identity and entry.name in database_files | {"identity.json"}
                ):
                    raise ResearchWorkspaceError("unrecognized initialization staging record")
            if not has_identity:
                self._publish(staging / "identity.json", self._manifest.encode("utf-8"))
            # Never remove the bound identity while recovering, even briefly.
            for entry in entries:
                if entry.name in database_files or entry.name.startswith(".pending-"):
                    entry.unlink()
            (staging / "blobs").mkdir(exist_ok=True)
            _sync_directory(staging)
            self._initialize_database()
            self.snapshot()
            _sync_directory(staging)
            os.rename(staging, destination)
            _sync_directory(destination.parent)
        finally:
            self.root = destination

    @contextmanager
    def execution_lock(self):
        """Yield a transferable lease; fail promptly if a prior lease is active."""
        self._check_manifest()
        with _LEASE_GUARD:
            if self.root in _ACTIVE_LEASES:
                raise ResearchWorkspaceError("workspace execution lock is already held")
            path = self._safe_file(self.root / ".execution.lock")
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ResearchWorkspaceError("invalid workspace execution lock")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ResearchWorkspaceError("workspace execution lock is already held") from exc
                lease = ExecutionLease(self.root, fd)
                _ACTIVE_LEASES[self.root] = lease
            except BaseException:
                os.close(fd)
                raise
        try:
            self.snapshot()
            yield lease
        finally:
            lease._context_exited()

    def _initialize_database(self):
        conn = sqlite3.connect(str(self.root / "workspace.sqlite3"), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            for statement in (
                "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "schema_version TEXT NOT NULL, identity_json TEXT NOT NULL, identity_sha256 TEXT NOT NULL)",
                "CREATE TABLE artifacts (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, "
                "path TEXT NOT NULL, kind TEXT NOT NULL, record_sha256 TEXT NOT NULL, "
                "searchable INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, inputs_json TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('pending','done')), receipt_json TEXT, "
                "record_sha256 TEXT NOT NULL)",
                "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
                "kind TEXT NOT NULL, payload_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)",
                "CREATE INDEX events_by_task ON events(task_id, id)",
                "CREATE INDEX events_by_kind ON events(kind, id)",
                "CREATE TABLE discoveries (id TEXT PRIMARY KEY, question TEXT NOT NULL, "
                "origin_task TEXT NOT NULL, status TEXT NOT NULL, task_id TEXT, "
                "evidence_refs_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)",
            ):
                conn.execute(statement)
            conn.execute("INSERT INTO metadata VALUES (1,?,?,?)",
                         (SCHEMA_VERSION, self._identity_json, self._identity_sha))
            conn.commit()
        finally:
            conn.close()

    def _ensure_event_kind_index(self):
        """Add the query index to v1 stores without changing any stored records."""
        with self._transaction(write=True) as conn:
            conn.execute("CREATE INDEX IF NOT EXISTS events_by_kind ON events(kind, id)")
            if [row[2] for row in conn.execute("PRAGMA index_info(events_by_kind)")] != ["kind", "id"]:
                raise ResearchWorkspaceError("workspace event-kind index is corrupt")

    def _ensure_search_index(self):
        """Migrate only after base-store verification, atomically and once.

        The FTS index stores terms, not a second full copy of retained text.
        Authenticated chunk metadata maps hits to bounded original blob reads.
        No corpus/index creation or reconciliation occurs on the query path.
        """
        with self._transaction(write=True) as conn:
            self._ensure_search_eligibility(conn)
            present = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('evidence_chunks','evidence_search')",
            )}
            if present:
                if len(present) != 2:
                    raise ResearchWorkspaceError("incomplete evidence search index")
                self._validate_search_index(conn)
                return
            conn.execute("CREATE TABLE evidence_chunks (id INTEGER PRIMARY KEY, artifact_id TEXT NOT NULL, "
                         "start INTEGER NOT NULL, end INTEGER NOT NULL, byte_start INTEGER NOT NULL, "
                         "byte_end INTEGER NOT NULL, sha256 TEXT NOT NULL, record_sha256 TEXT NOT NULL)")
            conn.execute("CREATE UNIQUE INDEX evidence_chunks_by_artifact ON evidence_chunks(artifact_id,start)")
            conn.execute("CREATE VIRTUAL TABLE evidence_search USING fts5(terms, content='')")
            for row in conn.execute("SELECT * FROM artifacts ORDER BY rowid"):
                record = self._verify("artifact", row)
                if record["searchable"]:
                    self._index_artifact(conn, record, record["kind"], self._artifact(conn, record))

    def _ensure_search_eligibility(self, conn):
        """Migrate internal eligibility without relabeling original evidence.

        A SHA first seen in a streamed fragment may later be a complete result.
        Its first kind/provenance remains intact; only discovery eligibility is
        monotonic. Old indexed stores establish this from their existing chunk
        membership as well as the original kind, then verify index completeness.
        This migration occurs only after full base-store verification.
        """
        if "searchable" in {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}:
            return
        has_chunks = conn.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_chunks'").fetchone()
        conn.execute("ALTER TABLE artifacts ADD COLUMN searchable INTEGER NOT NULL DEFAULT 0")
        for row in conn.execute("SELECT * FROM artifacts"):
            old = dict(row)
            old.pop("searchable")
            record = self._verify("artifact", old)
            was_indexed = has_chunks and conn.execute(
                "SELECT 1 FROM evidence_chunks WHERE artifact_id=? LIMIT 1", (record["id"],),
            ).fetchone()
            record["searchable"] = int(bool(record["bytes"] and (
                record["kind"] not in _SEARCH_SKIP_KINDS or was_indexed)))
            conn.execute("UPDATE artifacts SET searchable=?,record_sha256=? WHERE id=?",
                         (record["searchable"], self._digest("artifact", record), record["id"]))

    def _validate_search_index(self, conn):
        """Check startup completeness without copying retained artifact bodies.

        A structurally valid FTS table can still have lost all its postings.
        Verify both membership directions and contiguous authenticated chunk
        coverage of every searchable artifact. Base blobs were already verified
        by snapshot(); queries never run this corpus-wide metadata audit.
        """
        try:
            conn.execute("INSERT INTO evidence_search(evidence_search) VALUES ('integrity-check')")
        except sqlite3.Error as exc:
            raise ResearchWorkspaceError("corrupt evidence search index postings") from exc
        for query in (
            "SELECT id FROM evidence_chunks EXCEPT SELECT rowid FROM evidence_search",
            "SELECT rowid FROM evidence_search EXCEPT SELECT id FROM evidence_chunks",
            "SELECT artifact_id FROM evidence_chunks EXCEPT SELECT id FROM artifacts",
        ):
            if conn.execute("SELECT 1 FROM (" + query + ") LIMIT 1").fetchone():
                raise ResearchWorkspaceError("incomplete evidence search index membership")
        step = _SEARCH_CHARS - _SEARCH_OVERLAP
        for row in conn.execute("SELECT * FROM artifacts"):
            record = self._verify("artifact", row)
            previous = None
            for row in conn.execute("SELECT * FROM evidence_chunks WHERE artifact_id=? ORDER BY start",
                                    (record["id"],)):
                chunk = self._verify("evidence_chunk", row)
                if (not all(type(chunk[key]) is int for key in ("start", "end", "byte_start", "byte_end"))
                        or not 0 <= chunk["start"] < chunk["end"]
                        or chunk["end"] - chunk["start"] > _SEARCH_CHARS
                        or not 0 <= chunk["byte_start"] < chunk["byte_end"] <= record["bytes"]
                        or chunk["byte_end"] - chunk["byte_start"] > 4 * _SEARCH_CHARS
                        or previous is None and (chunk["start"] != 0 or chunk["byte_start"] != 0)
                        or previous is not None and (
                            chunk["start"] != previous["start"] + step
                            or previous["end"] != previous["start"] + _SEARCH_CHARS
                            or chunk["end"] <= previous["end"]
                            or not previous["byte_start"] < chunk["byte_start"] <= previous["byte_end"]
                            or chunk["byte_end"] <= previous["byte_end"]
                        )):
                    raise ResearchWorkspaceError("incomplete evidence search index ranges")
                previous = chunk
            if ((previous is None and record["searchable"])
                    or previous is not None and not record["searchable"]
                    or previous is not None and previous["byte_end"] != record["bytes"]):
                raise ResearchWorkspaceError("incomplete evidence search index artifact coverage")

    def _index_artifact(self, conn, ref, kind, text):
        record = self._verify("artifact", conn.execute("SELECT * FROM artifacts WHERE id=?", (ref["id"],)).fetchone())
        if not text or kind in _SEARCH_SKIP_KINDS and not record["searchable"]:
            return
        if not record["searchable"]:
            record["searchable"] = 1
            conn.execute("UPDATE artifacts SET searchable=1,record_sha256=? WHERE id=?",
                         (self._digest("artifact", record), ref["id"]))
        if conn.execute("SELECT 1 FROM evidence_chunks WHERE artifact_id=? LIMIT 1", (ref["id"],)).fetchone():
            return
        byte_start = 0
        step = _SEARCH_CHARS - _SEARCH_OVERLAP
        for start in range(0, len(text), step):
            end = min(start + _SEARCH_CHARS, len(text))
            excerpt = text[start:end]
            data = excerpt.encode("utf-8")
            values = {"artifact_id": ref["id"], "start": start, "end": end,
                      "byte_start": byte_start, "byte_end": byte_start + len(data),
                      "sha256": hashlib.sha256(data).hexdigest()}
            cursor = conn.execute("INSERT INTO evidence_chunks "
                                  "(artifact_id,start,end,byte_start,byte_end,sha256,record_sha256) "
                                  "VALUES (?,?,?,?,?,?,'')", tuple(values.values()))
            values = {"id": cursor.lastrowid, **values}
            conn.execute("UPDATE evidence_chunks SET record_sha256=? WHERE id=?",
                         (self._digest("evidence_chunk", values), values["id"]))
            conn.execute("INSERT INTO evidence_search(rowid,terms) VALUES (?,?)",
                         (values["id"], " ".join(_search_terms(excerpt))))
            if end == len(text):
                break
            byte_start += len(text[start:start + step].encode("utf-8"))

    def _read_evidence_chunk(self, conn, row):
        chunk = self._verify("evidence_chunk", row)
        record = conn.execute("SELECT * FROM artifacts WHERE id=?", (chunk["artifact_id"],)).fetchone()
        if record is None:
            raise ResearchWorkspaceError("artifact receipt missing")
        record = self._verify("artifact", record)
        ref = self._validate_ref(record)
        if (not all(type(chunk[key]) is int for key in ("start", "end", "byte_start", "byte_end"))
                or not 0 <= chunk["start"] < chunk["end"] <= ref["bytes"]
                or chunk["end"] - chunk["start"] > _SEARCH_CHARS
                or not 0 <= chunk["byte_start"] < chunk["byte_end"] <= ref["bytes"]
                or chunk["byte_end"] - chunk["byte_start"] > 4 * _SEARCH_CHARS):
            raise ResearchWorkspaceError("invalid evidence chunk range")
        path = self._safe_file(self.root / ref["path"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            status = os.fstat(stream.fileno())
            if not stat.S_ISREG(status.st_mode) or status.st_size != ref["bytes"]:
                raise ResearchWorkspaceError("artifact size or file type mismatch")
            stream.seek(chunk["byte_start"])
            data = stream.read(chunk["byte_end"] - chunk["byte_start"])
        if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
            raise ResearchWorkspaceError("evidence chunk hash mismatch")
        try:
            excerpt = data.decode("utf-8")
        except UnicodeError as exc:
            raise ResearchWorkspaceError("evidence chunk encoding is corrupt") from exc
        if len(excerpt) != chunk["end"] - chunk["start"]:
            raise ResearchWorkspaceError("evidence chunk character range mismatch")
        return {"artifact_id": ref["id"], "kind": _text(record["kind"], "artifact kind")[:80],
                "bytes": ref["bytes"], "excerpt": excerpt, "start": chunk["start"], "end": chunk["end"],
                "range_query": f'chars:{chunk["start"]}:{chunk["end"]}'}

    def search_evidence(self, query: str, limit: int = 10) -> dict:
        """Rank retained artifacts using literal terms and bounded exact ranges.

        Results are discovery data, never source-status or factual assertions.
        Each returned range is authenticated against its ingestion-time digest;
        full-blob verification remains part of initialization and read_artifact.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("query must contain 1 to 512 characters")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be an integer from 1 to 20")
        terms = _search_terms(query)
        if len(terms) > 64:
            raise ValueError("query contains more than 64 search terms")
        results = []
        with self._transaction() as conn:
            if terms:
                # No caller syntax reaches MATCH: punctuation/operators are
                # literal quoted words, combined by our own OR expression.
                match = " OR ".join('"' + term + '"' for term in terms)
                rows = conn.execute(
                    "WITH hits AS MATERIALIZED (SELECT c.*, bm25(evidence_search) AS score "
                    "FROM evidence_search JOIN evidence_chunks c ON c.id=evidence_search.rowid "
                    "WHERE evidence_search MATCH ?), "
                    "ranked AS (SELECT id, row_number() OVER (PARTITION BY artifact_id "
                    "ORDER BY score,start) AS choice,score FROM hits) "
                    "SELECT c.* FROM ranked r JOIN evidence_chunks c ON c.id=r.id "
                    "WHERE r.choice=1 ORDER BY r.score,c.artifact_id LIMIT ?", (match, limit),
                ).fetchall()
                results = [self._read_evidence_chunk(conn, row) for row in rows]
        return {"query": query, "limit": limit, "results": results}

    @contextmanager
    def _transaction(self, *, write=False):
        self._check_manifest()
        conn = None
        try:
            db = self._safe_file(self.root / "workspace.sqlite3")
            # mode=rw is essential: a deleted database must never be recreated.
            conn = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise ResearchWorkspaceError("workspace WAL configuration is corrupt")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            rows = conn.execute("SELECT * FROM metadata").fetchall()
            if len(rows) != 1 or tuple(rows[0]) != (
                1, SCHEMA_VERSION, self._identity_json, self._identity_sha,
            ):
                raise ResearchWorkspaceError("workspace identity mismatch or corrupt metadata")
            yield conn
            conn.commit()
        except (OSError, sqlite3.Error) as exc:
            raise ResearchWorkspaceError("workspace storage unavailable or corrupt") from exc
        finally:
            if conn is not None:
                conn.close()

    def _digest(self, kind, values):
        return _sha(_json([SCHEMA_VERSION, self._identity_sha, kind, values]))

    def _verify(self, kind, row):
        values = dict(row)
        digest = values.pop("record_sha256")
        if digest != self._digest(kind, values):
            raise ResearchWorkspaceError(f"corrupt {kind} record")
        if kind == "artifact" and "searchable" in values and (
                type(values["searchable"]) is not int or values["searchable"] not in (0, 1)):
            raise ResearchWorkspaceError("corrupt artifact search eligibility")
        return values

    def _publish(self, destination, data):
        """Publish without replacement; concurrent identical writers share a blob."""
        self._safe_file(destination)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                if self._read_bytes(destination) != data:
                    raise ResearchWorkspaceError("corrupt or conflicting existing blob") from None
            _sync_directory(destination.parent)
        finally:
            os.unlink(temporary)

    @staticmethod
    def _validate_ref(ref):
        if type(ref) is not dict or not _REF_KEYS.issubset(ref):
            raise ResearchWorkspaceError("invalid artifact reference")
        digest = ref["sha256"]
        if (not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or ref["id"] != digest or type(ref["bytes"]) is not int or ref["bytes"] < 0
                or ref["path"] != f"blobs/{digest}.txt"):
            raise ResearchWorkspaceError("invalid artifact hash, size or path")
        return {key: ref[key] for key in ("id", "sha256", "bytes", "path")}

    def _artifact(self, conn, ref):
        ref = self._validate_ref(ref)
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (ref["id"],)).fetchone()
        if row is None:
            raise ResearchWorkspaceError("artifact receipt missing")
        record = self._verify("artifact", row)
        if ref != self._validate_ref(record):
            raise ResearchWorkspaceError("artifact receipt mismatch")
        _text(record["kind"], "artifact kind")
        data = self._read_bytes(self.root / ref["path"])
        if len(data) != ref["bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ResearchWorkspaceError("artifact content hash mismatch")
        try:
            return data.decode("utf-8")
        except UnicodeError as exc:
            raise ResearchWorkspaceError("artifact encoding is corrupt") from exc

    def _record_artifact(self, conn, ref, kind):
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (ref["id"],)).fetchone()
        if row is not None:
            self._artifact(conn, ref)
            return
        record = {**ref, "kind": kind, "searchable": 0}
        conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", (
            ref["id"], ref["sha256"], ref["bytes"], ref["path"], kind,
            self._digest("artifact", record), 0,
        ))

    def put_artifact(self, text: str, kind: str) -> dict:
        """Archive all text; kind is metadata and never part of a filesystem path."""
        if not isinstance(text, str):
            raise ResearchWorkspaceError("artifact text must be a string")
        _text(kind, "artifact kind")
        try:
            data = text.encode("utf-8")
        except UnicodeError as exc:
            raise ResearchWorkspaceError("artifact text is not UTF-8") from exc
        digest = hashlib.sha256(data).hexdigest()
        ref = {"id": digest, "sha256": digest, "bytes": len(data), "path": f"blobs/{digest}.txt"}
        with self._transaction(write=True) as conn:
            self._publish(self.root / ref["path"], data)
            self._record_artifact(conn, ref, kind)
            self._index_artifact(conn, ref, kind, text)
        return ref

    def read_artifact(self, ref: dict) -> str:
        with self._transaction() as conn:
            return self._artifact(conn, ref)

    def lookup_artifact(self, artifact_id: str) -> dict:
        """Resolve a hash, raising ResearchArtifactNotFoundError only for absence."""
        if not isinstance(artifact_id, str) or not _HASH.fullmatch(artifact_id):
            raise ResearchWorkspaceError("invalid artifact id")
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if row is None:
                raise ResearchArtifactNotFoundError("artifact receipt missing")
            ref = self._validate_ref(self._verify("artifact", row))
            self._artifact(conn, ref)
            return ref

    def lookup_ref(self, artifact_id: str) -> dict:
        """Compatibility name for scoped, verified artifact-ID resolution."""
        return self.lookup_artifact(artifact_id)

    def _embedded_refs(self, conn, value):
        if isinstance(value, dict):
            if _REF_KEYS.issubset(value):
                self._artifact(conn, value)
            # A reference may carry provenance metadata containing more refs.
            # Verifying its own four fields must not exempt those descendants.
            for item in value.values():
                self._embedded_refs(conn, item)
        elif isinstance(value, list):
            for item in value:
                self._embedded_refs(conn, item)

    def _task(self, conn, row):
        record = self._verify("task", row)
        _text(record["task_id"], "task id")
        inputs = _decode(record["inputs_json"])
        _object(inputs, "task inputs")
        self._embedded_refs(conn, inputs)
        if record["status"] == "pending" and record["receipt_json"] is None:
            return None
        if record["status"] != "done" or record["receipt_json"] is None:
            raise ResearchWorkspaceError("corrupt task completion state")
        receipt = _decode(self._artifact(conn, _decode(record["receipt_json"])))
        if (type(receipt) is not dict or receipt.get("schema_version") != "research-task/v1"
                or receipt.get("workspace_sha256") != self._identity_sha
                or receipt.get("task_id") != record["task_id"] or receipt.get("inputs") != inputs):
            raise ResearchWorkspaceError("task receipt identity mismatch")
        result = receipt.get("result")
        _object(result, "task result")
        self._embedded_refs(conn, result)
        return result

    def load_task(self, task_id: str, inputs: dict) -> dict | None:
        """Bind inputs on first admission; a pending task returns None on restart."""
        _text(task_id, "task id")
        inputs_json = _object(inputs, "task inputs")
        with self._transaction(write=True) as conn:
            self._embedded_refs(conn, _decode(inputs_json))
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is not None:
                result = self._task(conn, row)
                if row["inputs_json"] != inputs_json:
                    raise ResearchWorkspaceError("task inputs identity conflict")
                return result
            record = {"task_id": task_id, "inputs_json": inputs_json,
                      "status": "pending", "receipt_json": None}
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", (
                task_id, inputs_json, "pending", None, self._digest("task", record),
            ))
        return None

    def save_task(self, task_id, inputs, result: dict) -> None:
        """Durably archive the full receipt before committing the done marker."""
        inputs_json = _object(inputs, "task inputs")
        result_json = _object(result, "task result")
        frozen_inputs, frozen_result = _decode(inputs_json), _decode(result_json)
        existing = self.load_task(task_id, frozen_inputs)
        if existing is not None:
            if _json(existing) != result_json:
                raise ResearchWorkspaceError("completed task result conflict")
            return
        with self._transaction() as conn:
            self._embedded_refs(conn, frozen_result)
        receipt = self.put_artifact(_json({
            "schema_version": "research-task/v1", "workspace_sha256": self._identity_sha,
            "task_id": task_id, "inputs": frozen_inputs, "result": frozen_result,
        }), "task_receipt")
        with self._transaction(write=True) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise ResearchWorkspaceError("task admission record missing")
            existing = self._task(conn, row)
            if row["inputs_json"] != inputs_json:
                raise ResearchWorkspaceError("task inputs identity conflict")
            if existing is not None:
                if _json(existing) != result_json:
                    raise ResearchWorkspaceError("completed task result conflict")
                return
            self._artifact(conn, receipt)
            self._embedded_refs(conn, frozen_result)
            record = {"task_id": task_id, "inputs_json": inputs_json,
                      "status": "done", "receipt_json": _json(receipt)}
            conn.execute("UPDATE tasks SET status=?,receipt_json=?,record_sha256=? WHERE task_id=?", (
                "done", record["receipt_json"], self._digest("task", record), task_id,
            ))

    def _append_event(self, conn, task_id, kind, payload):
        payload_json = _object(payload, "event payload")
        self._embedded_refs(conn, _decode(payload_json))
        cursor = conn.execute("INSERT INTO events(task_id,kind,payload_json,record_sha256) "
                              "VALUES (?,?,?,'')", (task_id, kind, payload_json))
        event_id = cursor.lastrowid
        record = {"id": event_id, "task_id": task_id, "kind": kind, "payload_json": payload_json}
        conn.execute("UPDATE events SET record_sha256=? WHERE id=?",
                     (self._digest("event", record), event_id))
        return event_id

    def append_event(self, task_id, kind, payload: dict) -> int:
        _text(task_id, "task id")
        _text(kind, "event kind")
        with self._transaction(write=True) as conn:
            return self._append_event(conn, task_id, kind, payload)

    def _event(self, conn, row):
        record = self._verify("event", row)
        _text(record["task_id"], "task id")
        _text(record["kind"], "event kind")
        record["payload"] = _decode(record.pop("payload_json"))
        _object(record["payload"], "event payload")
        self._embedded_refs(conn, record["payload"])
        return record

    def events(self, task_id) -> list[dict]:
        _text(task_id, "task id")
        with self._transaction() as conn:
            return [self._event(conn, row) for row in conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,),
            )]

    def events_by_kind(self, kind: str) -> list[dict]:
        """Return matching verified events in ID order without scanning tasks.

        Rows have the same ``id, task_id, kind, payload`` shape as events(). Only
        selected event records and their recursively referenced artifacts are
        verified here, in addition to workspace identity. Unrelated task results
        and event kinds are not read. Payloads retain their original provenance;
        a kind label is a selection key, never a source-authority assertion.
        """
        _text(kind, "event kind")
        with self._transaction() as conn:
            return [self._event(conn, row) for row in conn.execute(
                "SELECT * FROM events WHERE kind=? ORDER BY id", (kind,),
            )]

    def _discovery(self, conn, row):
        record = self._verify("discovery", row)
        for key in ("question", "origin_task", "status"):
            _text(record[key], "discovery " + key)
        if record["id"] != _sha(" ".join(record["question"].split())):
            raise ResearchWorkspaceError("discovery identity mismatch")
        if record["task_id"] is not None:
            _text(record["task_id"], "discovery task id")
        refs = _decode(record.pop("evidence_refs_json"))
        if type(refs) is not list:
            raise ResearchWorkspaceError("invalid discovery evidence")
        for ref in refs:
            self._artifact(conn, ref)
        record["evidence_refs"] = refs
        return record

    def add_discovery(self, question: str, origin_task: str, evidence_refs: list = ()) -> dict:
        """Deduplicate whitespace-equivalent questions, retaining every observation."""
        _text(question, "discovery question")
        _text(origin_task, "discovery origin")
        if not isinstance(evidence_refs, (list, tuple)):
            raise ResearchWorkspaceError("invalid discovery evidence")
        refs = [self._validate_ref(ref) for ref in evidence_refs]
        discovery_id = _sha(" ".join(question.split()))
        with self._transaction(write=True) as conn:
            for ref in refs:
                self._artifact(conn, ref)
            row = conn.execute("SELECT * FROM discoveries WHERE id=?", (discovery_id,)).fetchone()
            record = self._discovery(conn, row) if row is not None else {
                "id": discovery_id, "question": question, "origin_task": origin_task,
                "status": "pending", "task_id": None, "evidence_refs": [],
            }
            for ref in refs:
                if ref not in record["evidence_refs"]:
                    record["evidence_refs"].append(ref)
            self._write_discovery(conn, record)
            self._append_event(conn, origin_task, "discovery_observed", {
                "discovery_id": discovery_id, "question": question, "evidence_refs": refs,
            })
            return record

    def _write_discovery(self, conn, record):
        values = {key: value for key, value in record.items() if key != "evidence_refs"}
        values["evidence_refs_json"] = _json(record["evidence_refs"])
        conn.execute("INSERT INTO discoveries VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                     "status=excluded.status,task_id=excluded.task_id,"
                     "evidence_refs_json=excluded.evidence_refs_json,record_sha256=excluded.record_sha256", (
                         values["id"], values["question"], values["origin_task"], values["status"],
                         values["task_id"], values["evidence_refs_json"], self._digest("discovery", values),
                     ))

    def discoveries(self, status: str | None = None) -> list[dict]:
        if status is not None:
            _text(status, "discovery status")
        with self._transaction() as conn:
            query = "SELECT * FROM discoveries"
            params = ()
            if status is not None:
                query += " WHERE status=?"
                params = (status,)
            return [self._discovery(conn, row) for row in conn.execute(query + " ORDER BY rowid", params)]

    def mark_discovery(self, discovery_id, status, task_id=None) -> None:
        _text(discovery_id, "discovery id")
        _text(status, "discovery status")
        if task_id is not None:
            _text(task_id, "task id")
        with self._transaction(write=True) as conn:
            row = conn.execute("SELECT * FROM discoveries WHERE id=?", (discovery_id,)).fetchone()
            if row is None:
                raise KeyError(discovery_id)
            record = self._discovery(conn, row)
            record["status"] = status
            if task_id is not None:
                record["task_id"] = task_id
            self._write_discovery(conn, record)

    def snapshot(self) -> dict:
        """Return a consistent, fully verified view; no prompt-size truncation.

        Public shape: ``{schema_version, identity, artifacts, tasks, events,
        discoveries}``. Artifacts are the four-field references returned by
        put_artifact. Tasks contain ``task_id, inputs, status, result`` (pending
        results are None). Events contain ``id, task_id, kind, payload`` in ID
        order. Discoveries contain ``id, question, origin_task, status, task_id,
        evidence_refs`` in creation order. Use the identity property for a cheap
        detached identity copy; snapshot deliberately reads all archived text.
        """
        with self._transaction() as conn:
            if [row[0] for row in conn.execute("PRAGMA quick_check")] != ["ok"]:
                raise ResearchWorkspaceError("workspace database integrity check failed")
            artifacts = []
            for row in conn.execute("SELECT * FROM artifacts ORDER BY rowid"):
                ref = self._validate_ref(self._verify("artifact", row))
                self._artifact(conn, ref)
                artifacts.append(ref)
            tasks = []
            for row in conn.execute("SELECT * FROM tasks ORDER BY rowid"):
                result = self._task(conn, row)
                tasks.append({"task_id": row["task_id"], "inputs": _decode(row["inputs_json"]),
                              "status": row["status"], "result": result})
            events = [self._event(conn, row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
            discoveries = [self._discovery(conn, row) for row in conn.execute(
                "SELECT * FROM discoveries ORDER BY rowid",
            )]
            return {"schema_version": SCHEMA_VERSION, "identity": _decode(self._identity_json),
                    "artifacts": artifacts, "tasks": tasks, "events": events, "discoveries": discoveries}
