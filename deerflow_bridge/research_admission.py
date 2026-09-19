"""Durable local input-token admission for one active agentic workspace.

This ledger is separate from immutable research tasks. It reserves serialized
UTF-8 bytes plus framing overhead before a send, then records standardized
LangChain usage_metadata. Input tokens include cache tokens once; output tokens
are reported separately and do not consume the prompt allowance. Estimates are
not tokenizer measurements, provider guarantees or invoice reconciliation.

Use one outer model_admission scope per logical model call. Nested wrappers
share its ticket through ContextVars, including inherited async contexts. If a
nested wrapper exposes larger tool schemas it must extend the reservation before
sending. Independent calls must have independent outer scopes. Every normal or
exceptional exit settles unreported usage conservatively. Process death leaves
its reservation held permanently: restarting never refunds uncertain usage.

The ledger pins its initial budget (default 4,000,000; explicit 0 is unlimited).
Changing that setting on resume fails closed, rather than resetting allowance.
Only hashes, operation IDs, counters and static state/reason codes are stored.
No prompt, tool schema, response content or credentials enter the database.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, copy_context
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
import threading
import uuid


SCHEMA_VERSION = "research-model-admission/v1"
DEFAULT_PROMPT_BUDGET_TOKENS = 4_000_000
_MAX_INT = 2**63 - 1
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CURRENT = ContextVar("research_model_admission_ticket", default=None)
_DEPTH = ContextVar("research_model_admission_depth", default=0)
ESTIMATE_BASIS = "UTF-8 serialized input bytes + framing reserve; estimated, not tokenized or invoiced"


def _stop():
    from research_compaction import ResearchCompactionError, stop_after_compaction_failure
    error = ResearchCompactionError("checkpoint_unavailable")
    stop_after_compaction_failure(error)
    raise error from None


def _active_workspace():
    if os.environ.get("RESEARCH_ENGINE", "").strip().lower() != "agentic":
        return None
    import research_archive
    return research_archive.current_workspace()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalized(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _normalized(item) for key, item in value.items()}
    if callable(getattr(value, "model_dump", None)):
        return _normalized(value.model_dump(mode="json"))
    if callable(getattr(value, "dict", None)):
        return _normalized(value.dict())
    raise ValueError("unsupported model input serialization")


def _tool_schema(tool):
    if isinstance(tool, dict):
        return _normalized(tool)
    name, description = getattr(tool, "name", None), getattr(tool, "description", None)
    if not isinstance(name, str) or not isinstance(description, str):
        raise ValueError("tool schema identity unavailable")
    schema = getattr(tool, "args_schema", None)
    if schema is None and callable(getattr(tool, "get_input_schema", None)):
        schema = tool.get_input_schema()
    if callable(getattr(schema, "model_json_schema", None)):
        schema = schema.model_json_schema()
    elif callable(getattr(schema, "schema", None)):
        schema = schema.schema()
    if not isinstance(schema, dict):
        raise ValueError("tool input schema unavailable")
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": _normalized(schema),
    }}


def _estimate(messages, tools):
    if tools is not None and not isinstance(tools, (list, tuple)):
        raise ValueError("tools must be a schema sequence")
    serialized = _json({"messages": _normalized(messages),
                        "tools": [_tool_schema(tool) for tool in tools or ()]}).encode("utf-8")
    message_count = len(messages) if isinstance(messages, (list, tuple)) else 1
    estimate = len(serialized) + 256 + 32 * message_count + 64 * len(tools or ())
    if estimate > _MAX_INT:
        raise ValueError("input estimate exceeds ledger capacity")
    return hashlib.sha256(serialized).hexdigest(), estimate


def _configured_limit():
    raw = os.environ.get("RESEARCH_AGENTIC_PROMPT_BUDGET_TOKENS", str(DEFAULT_PROMPT_BUDGET_TOKENS)).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError("invalid prompt budget")
    value = int(raw)
    if value > _MAX_INT:
        raise ValueError("prompt budget exceeds ledger capacity")
    return value


def _count(value):
    return type(value) is int and 0 <= value <= _MAX_INT


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _usage(response):
    """Return chargeable standardized counts; never add cache detail partitions."""
    result = _get(response, "result", response)
    candidates = result if isinstance(result, (list, tuple)) else [result]
    selected = [item for item in candidates if _get(item, "type") not in {"tool", "human", "system"}]
    inputs = outputs = 0
    input_seen = output_seen = False
    known = bool(selected)
    output_complete = bool(selected)
    reason = "missing_usage"
    seen = set()
    for item in selected:
        if id(item) in seen:
            continue
        seen.add(id(item))
        usage = _get(item, "usage_metadata")
        if not isinstance(usage, dict) or not usage:
            known = False
            output_complete = False
            continue
        incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
        valid_in, valid_out = _count(incoming) and incoming > 0, _count(outgoing)
        if valid_in:
            inputs += incoming
            input_seen = True
        if valid_out:
            outputs += outgoing
            output_seen = True
        else:
            output_complete = False
        details_valid = True
        if "total_tokens" in usage:
            details_valid = _count(usage["total_tokens"])
            if valid_in and valid_out:
                details_valid = details_valid and usage["total_tokens"] == incoming + outgoing
        for key in ("input_token_details", "output_token_details"):
            details = usage.get(key)
            if details is not None:
                details_valid = details_valid and isinstance(details, dict) and all(_count(v) for v in details.values())
        cache = usage.get("input_token_details")
        if valid_in and isinstance(cache, dict):
            read, creation = cache.get("cache_read", 0), cache.get("cache_creation", 0)
            # These two cache partitions are disjoint parts of inclusive input.
            # Other detail categories may overlap, so never sum arbitrary keys.
            details_valid = (details_valid and _count(read) and _count(creation)
                             and read + creation <= incoming)
        if not valid_in or not valid_out or not details_valid:
            known = False
            reason = "invalid_usage"
    if _get(response, "error") or _get(response, "status") in ("error", "failed", "cancelled"):
        known = False
        reason = "failed_response"
    if inputs > _MAX_INT or outputs > _MAX_INT:
        return None, None, False, "invalid_usage"
    return (inputs if input_seen else None, outputs if output_seen and output_complete else None,
            known, "reported" if known else reason)


class _Ledger:
    def __init__(self, workspace):
        self.root = Path(workspace.root).resolve()
        self.path = self.root / "model_usage.sqlite3"
        self.marker = self.root / ".model_usage.lock"
        self.staging = self.root / ".model_usage.staging.sqlite3"
        self.identity = hashlib.sha256(_json(workspace.identity).encode("utf-8")).hexdigest()

    @property
    def header(self):
        return _json([SCHEMA_VERSION, self.identity]).encode("ascii")

    def _safe_paths(self):
        if not self.root.is_dir():
            raise ValueError("workspace unavailable")
        for path in (self.path, self.marker, self.staging, Path(str(self.path) + "-wal"),
                     Path(str(self.path) + "-shm"), Path(str(self.staging) + "-wal"),
                     Path(str(self.staging) + "-shm")):
            if path.is_symlink():
                raise ValueError("unsafe admission ledger path")

    def initialize(self, limit):
        """Publish initialized SQLite before the single-byte ready transition.

        An identity-bound pending marker permits rebuilding only the unpublished
        staging database. The ready marker is fsynced before any reservation;
        missing/corrupt published usage is never recreated. The former header-
        only marker is accepted only with its existing valid database, retaining
        compatibility with already-created ledgers.
        """
        self._safe_paths()
        fd = os.open(self.marker, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("invalid ledger marker")
            fcntl.flock(fd, fcntl.LOCK_EX)
            marker = os.read(fd, 4096)
            pending, ready = self.header + b"\nP", self.header + b"\nPR"
            if not marker:
                if self.path.exists():
                    raise ValueError("ledger identity marker missing")
                if os.write(fd, pending) != len(pending):
                    raise OSError("incomplete ledger marker")
                os.fsync(fd)
                marker = pending
            if marker == pending:
                if not self.path.exists():
                    for path in (self.staging, Path(str(self.staging) + "-wal"), Path(str(self.staging) + "-shm")):
                        if path.exists():
                            if not path.is_file():
                                raise ValueError("invalid unpublished ledger")
                            path.unlink()
                    self._create(limit, self.staging)
                    staged_fd = os.open(self.staging, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        os.fsync(staged_fd)
                    finally:
                        os.close(staged_fd)
                    os.replace(self.staging, self.path)
                    directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                with self.transaction() as (conn, meta):
                    if meta["limit_input_tokens"] != limit or self.summary(conn, meta)["calls"]:
                        raise ValueError("unpublished ledger contains usage or conflicting policy")
                os.lseek(fd, 0, os.SEEK_END)
                if os.write(fd, b"R") != 1:
                    raise OSError("incomplete ledger readiness marker")
                os.fsync(fd)
            elif marker not in (self.header, ready) or not self.path.is_file():
                raise ValueError("ledger missing or identity conflict")
            with self.transaction() as (_, meta):
                if meta["limit_input_tokens"] != limit:
                    raise ValueError("persisted prompt budget configuration conflict")
        finally:
            os.close(fd)

    def _create(self, limit, path=None):
        conn = sqlite3.connect(str(path or self.path), timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                         "schema_version TEXT NOT NULL, workspace_sha256 TEXT NOT NULL, "
                         "limit_input_tokens INTEGER NOT NULL CHECK(limit_input_tokens>=0), "
                         "budget_denied_reason TEXT)")
            conn.execute("CREATE TABLE calls (op_id TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, "
                         "estimate_input_tokens INTEGER NOT NULL CHECK(estimate_input_tokens>0), "
                         "status TEXT NOT NULL, charged_input_tokens INTEGER NOT NULL CHECK(charged_input_tokens>=0), "
                         "input_tokens INTEGER, output_tokens INTEGER, coverage TEXT NOT NULL, reason TEXT NOT NULL)")
            conn.execute("INSERT INTO metadata VALUES (1,?,?,?,NULL)", (SCHEMA_VERSION, self.identity, limit))
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def inspection(self):
        """Wait for concurrent initialization without creating or repairing it."""
        self._safe_paths()
        if not self.marker.exists() and not self.path.exists():
            yield "absent"
            return
        fd = os.open(self.marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            marker = os.read(fd, 4096)
            if marker == self.header + b"\nP" or not marker and not self.path.exists():
                yield "pending"
            elif marker in (self.header, self.header + b"\nPR") and self.path.is_file():
                yield "ready"
            else:
                raise ValueError("ledger missing or identity conflict")
        finally:
            os.close(fd)

    @contextmanager
    def transaction(self, write=False):
        self._safe_paths()
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise ValueError("invalid ledger journal")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            rows = conn.execute("SELECT * FROM metadata").fetchall()
            if len(rows) != 1:
                raise ValueError("invalid ledger metadata")
            meta = dict(rows[0])
            if (meta["singleton"] != 1 or meta["schema_version"] != SCHEMA_VERSION
                    or meta["workspace_sha256"] != self.identity
                    or not _count(meta["limit_input_tokens"])
                    or meta["budget_denied_reason"] not in (None, "budget_denied")):
                raise ValueError("ledger identity or metadata corrupt")
            yield conn, meta
            conn.commit()
        finally:
            conn.close()

    def summary(self, conn, meta):
        calls = [dict(row) for row in conn.execute("SELECT * FROM calls ORDER BY rowid")]
        for row in calls:
            if (not _SHA.fullmatch(row["input_sha256"])
                    or not _count(row["estimate_input_tokens"]) or row["estimate_input_tokens"] == 0
                    or not _count(row["charged_input_tokens"])
                    or any(row[k] is not None and not _count(row[k]) for k in ("input_tokens", "output_tokens"))
                    or row["status"] not in {"reserved", "settled", "denied"}
                    or row["coverage"] not in {"known", "estimated", "unknown"}):
                raise ValueError("invalid admission counters")
            if row["status"] != "settled" and row["charged_input_tokens"] != 0:
                raise ValueError("invalid unsettled charge")
            if row["status"] == "settled":
                if row["coverage"] == "known":
                    if (row["input_tokens"] is None or row["output_tokens"] is None
                            or row["input_tokens"] <= 0 or row["charged_input_tokens"] != row["input_tokens"]):
                        raise ValueError("invalid reported charge")
                elif row["charged_input_tokens"] < row["estimate_input_tokens"]:
                    raise ValueError("uncertain usage was undercharged")
        settled = [row for row in calls if row["status"] == "settled"]
        reserved = [row for row in calls if row["status"] == "reserved"]
        spent = sum(row["charged_input_tokens"] for row in settled)
        held = sum(row["estimate_input_tokens"] for row in reserved)
        unknown = sum(row["coverage"] != "known" for row in settled) + len(reserved)
        limit = meta["limit_input_tokens"]
        return {
            "schema_version": SCHEMA_VERSION, "workspace_sha256": self.identity,
            "limit_input_tokens": limit, "spent_input_tokens": spent, "held_input_tokens": held,
            "remaining_input_tokens": max(0, limit - spent - held) if limit else None,
            "observed_input_tokens": sum(row["input_tokens"] or 0 for row in settled),
            "observed_output_tokens": sum(row["output_tokens"] or 0 for row in settled),
            "settled_calls": len(settled), "reserved_calls": len(reserved),
            "known_usage_calls": len(settled) - (unknown - len(reserved)),
            "estimated_calls": sum(row["coverage"] == "estimated" for row in settled),
            "unknown_usage_calls": unknown,
            "unknown_output_calls": sum(row["output_tokens"] is None for row in settled) + len(reserved),
            "denied_calls": sum(row["status"] == "denied" for row in calls),
            "coverage": "estimated_or_unknown" if unknown else "reported",
            "estimate_basis": ESTIMATE_BASIS, "budget_denied_reason": meta["budget_denied_reason"],
            "calls": calls,
        }

    def reserve(self, input_hash, estimate):
        op_id = uuid.uuid4().hex
        with self.transaction(write=True) as (conn, meta):
            state = self.summary(conn, meta)
            denied = bool(meta["budget_denied_reason"]) or (
                meta["limit_input_tokens"] > 0
                and state["spent_input_tokens"] + state["held_input_tokens"] + estimate > meta["limit_input_tokens"]
            )
            conn.execute("INSERT INTO calls VALUES (?,?,?,?,0,NULL,NULL,'unknown',?)", (
                op_id, input_hash, estimate, "denied" if denied else "reserved",
                "budget_denied" if denied else "reserved",
            ))
            if denied:
                conn.execute("UPDATE metadata SET budget_denied_reason='budget_denied'")
        if denied:
            _stop()
        return _Ticket(self, op_id)


class _Ticket:
    def __init__(self, ledger, op_id):
        self.ledger = ledger
        self.op_id = op_id
        self._guard = threading.Lock()
        self._settled = False
        self._closed = False

    @property
    def active(self):
        with self._guard:
            return not self._settled and not self._closed

    def extend(self, input_hash, estimate):
        with self._guard:
            with self.ledger.transaction(write=True) as (conn, meta):
                state = self.ledger.summary(conn, meta)
                row = conn.execute("SELECT * FROM calls WHERE op_id=?", (self.op_id,)).fetchone()
                if row is None or row["status"] != "reserved":
                    raise ValueError("missing active admission")
                delta = max(0, estimate - row["estimate_input_tokens"])
                denied = bool(meta["budget_denied_reason"]) or (meta["limit_input_tokens"] > 0
                    and state["spent_input_tokens"] + state["held_input_tokens"] + delta > meta["limit_input_tokens"])
                if denied:
                    conn.execute("UPDATE metadata SET budget_denied_reason='budget_denied'")
                elif delta:
                    conn.execute("UPDATE calls SET input_sha256=?,estimate_input_tokens=? WHERE op_id=?",
                                 (input_hash, estimate, self.op_id))
            if denied:
                _stop()

    def settle(self, response):
        """Persist usage once; repeated settlement by nested wrappers is a no-op."""
        try:
            self._settle(response, "missing_usage")
        except Exception:
            _stop()

    def _settle(self, response, failure_reason):
        with self._guard:
            if self._settled:
                return
            inputs, outputs, known, reason = _usage(response) if response is not None else (None, None, False, failure_reason)
            with self.ledger.transaction(write=True) as (conn, meta):
                self.ledger.summary(conn, meta)
                row = conn.execute("SELECT * FROM calls WHERE op_id=?", (self.op_id,)).fetchone()
                if row is None or row["status"] != "reserved":
                    raise ValueError("missing active admission")
                charge = inputs if known else max(row["estimate_input_tokens"], inputs or 0)
                conn.execute("UPDATE calls SET status='settled',charged_input_tokens=?,input_tokens=?,"
                             "output_tokens=?,coverage=?,reason=? WHERE op_id=?", (
                                 charge, inputs, outputs, "known" if known else "estimated", reason, self.op_id,
                             ))
                state = self.ledger.summary(conn, meta)
                exceeded = meta["limit_input_tokens"] > 0 and (
                    state["spent_input_tokens"] + state["held_input_tokens"] > meta["limit_input_tokens"])
                if exceeded:
                    conn.execute("UPDATE metadata SET budget_denied_reason='budget_denied'")
            self._settled = True
            if exceeded:
                _stop()

    def close(self, failed):
        try:
            self._settle(None, "call_failed" if failed else "unsettled_response")
        except Exception:
            _stop()
        finally:
            with self._guard:
                self._closed = True


class _NoopTicket:
    def settle(self, response):
        pass


@contextmanager
def model_admission(messages, tools=None):
    """Reserve before invoking a model; yield a ticket with settle(response)."""
    workspace = _active_workspace()
    if workspace is None:
        yield _NoopTicket()
        return
    from research_compaction import raise_if_compaction_stopped
    raise_if_compaction_stopped()
    try:
        input_hash, estimate = _estimate(messages, tools)
        ledger = _Ledger(workspace)
        current = _CURRENT.get()
        nested = current is not None and current.active
        if nested:
            if current.ledger.path != ledger.path or current.ledger.identity != ledger.identity:
                raise ValueError("nested workspace identity conflict")
            current.extend(input_hash, estimate)
            ticket = current
        else:
            ledger.initialize(_configured_limit())
            ticket = ledger.reserve(input_hash, estimate)
    except Exception:
        _stop()
    token = _CURRENT.set(ticket)
    depth = _DEPTH.set(_DEPTH.get() + 1 if nested else 1)
    failed = True
    try:
        # Admission may wait on a database writer after its initial stop check.
        # A sibling failure during that wait must prohibit the physical send.
        raise_if_compaction_stopped()
        yield ticket
        failed = False
    finally:
        try:
            if not nested or failed:
                # A failed inner send is a real uncertain attempt even when an
                # outer wrapper catches it. Settle it now; a retry cannot reuse
                # this closed ticket and must reserve its own allowance.
                ticket.close(failed)
        finally:
            _DEPTH.reset(depth)
            _CURRENT.reset(token)


class _AsyncIOContext:
    """Run all blocking lifecycle steps in the same retained Context.

    Context-manager tokens belong to a Context, not a thread. Serializing entry,
    settlement and exit into this exact Context permits executor thread changes
    without moving tokens into the event loop's distinct Context.
    """

    def __init__(self):
        self.context = copy_context()
        self.guard = threading.Lock()

    async def run(self, action):
        def blocking():
            with self.guard:
                return self.context.run(action)
        future = asyncio.get_running_loop().run_in_executor(None, blocking)
        cancelled = None
        while True:
            try:
                return await asyncio.shield(future), cancelled
            except asyncio.CancelledError as exc:
                # Never abandon a worker that may still reserve or settle usage.
                # Repeated cancellation is deferred until that worker finishes.
                cancelled = cancelled or exc
                if future.done():
                    return future.result(), cancelled


class _AsyncTicket:
    def __init__(self, ticket, io):
        self._ticket = ticket
        self._io = io

    @property
    def op_id(self):
        return self._ticket.op_id

    async def settle(self, response):
        _, cancelled = await self._io.run(lambda: self._ticket.settle(response))
        if cancelled is not None:
            raise cancelled from None


class _AsyncNoopTicket:
    async def settle(self, response):
        pass


@asynccontextmanager
async def async_model_admission(messages, tools=None):
    """Async counterpart: await ticket.settle(response); no ledger I/O on loop.

    Cancellation drains admission/settlement/exit workers before propagating.
    Event-loop ContextVars have their own tokens but reference the same raw
    ticket, so nested sync or async wrappers continue to count one model call.
    """
    if _active_workspace() is None:
        yield _AsyncNoopTicket()
        return
    io = _AsyncIOContext()
    manager = model_admission(messages, tools)
    ticket, cancelled = await io.run(manager.__enter__)
    if cancelled is not None:
        await io.run(lambda: manager.__exit__(type(cancelled), cancelled, cancelled.__traceback__))
        raise cancelled from None
    token = _CURRENT.set(ticket)
    depth = _DEPTH.set(io.context.get(_DEPTH))
    try:
        try:
            from research_compaction import raise_if_compaction_stopped
            # Recheck after the executor handoff, immediately before caller send.
            raise_if_compaction_stopped()
            yield _AsyncTicket(ticket, io)
        except BaseException:
            exc = sys.exc_info()
            suppressed, cancelled = await io.run(lambda: manager.__exit__(*exc))
            if cancelled is not None:
                from research_compaction import ResearchCompactionError
                # Drain first, then honor cancellation over an ordinary provider
                # failure. Preserve an unsuppressed typed integrity stop instead;
                # cancellation must not conceal a broken evidence/accounting gate.
                if suppressed or not isinstance(exc[1], ResearchCompactionError):
                    raise cancelled from None
            if not suppressed:
                raise
        else:
            _, cancelled = await io.run(lambda: manager.__exit__(None, None, None))
            if cancelled is not None:
                raise cancelled from None
    finally:
        _DEPTH.reset(depth)
        _CURRENT.reset(token)


def snapshot(workspace) -> dict:
    """Return verified counters and hash-only call rows without resetting usage.

    Core fields are limit_input_tokens, spent_input_tokens, held_input_tokens,
    remaining_input_tokens (None for unlimited), observed_input/output_tokens,
    reserved/settled/known_usage/estimated/unknown_usage/unknown_output/denied_calls,
    coverage, estimate_basis, budget_denied_reason and calls. Observed sums are
    explicitly partial when the corresponding unknown counts are nonzero.
    """
    try:
        ledger = _Ledger(workspace)
        with ledger.inspection() as readiness:
            if not ledger.path.exists():
                # No calls yet: initialize on first admission, never from a read.
                limit = _configured_limit()
                return {
                    "schema_version": SCHEMA_VERSION, "workspace_sha256": ledger.identity,
                    "limit_input_tokens": limit, "spent_input_tokens": 0, "held_input_tokens": 0,
                    "remaining_input_tokens": limit or None, "observed_input_tokens": 0,
                    "observed_output_tokens": 0, "settled_calls": 0, "reserved_calls": 0,
                    "known_usage_calls": 0, "estimated_calls": 0, "unknown_usage_calls": 0,
                    "unknown_output_calls": 0, "denied_calls": 0, "coverage": "reported",
                    "estimate_basis": ESTIMATE_BASIS, "budget_denied_reason": None, "calls": [],
                }
            with ledger.transaction() as (conn, meta):
                result = ledger.summary(conn, meta)
                if readiness == "pending" and result["calls"]:
                    raise ValueError("unpublished ledger contains usage")
                return result
    except Exception:
        _stop()


# Deployed helpers use bare imports while repository fixtures may use the
# namespace package. They must share the same ContextVars and nested-call ticket.
sys.modules.setdefault("research_admission", sys.modules[__name__])
sys.modules.setdefault("deerflow_bridge.research_admission", sys.modules[__name__])
