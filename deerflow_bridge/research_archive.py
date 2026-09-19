"""Run-scoped durable evidence adapters for the new agentic engine.

The parent activates one workspace per child process before starting workers.
The registry is process-wide so native worker threads share it; it never opens
caller-supplied paths. Archive IDs resolve only through the active workspace's
verified registry. Source events are written only by cached_fetch's successful
producer path. Ordinary tool results, task answers and snippets cannot create
fetched-source records. Events do not create/complete research tasks.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import copy
import hashlib
import json
import math
import os
import re
import threading
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from research_compaction import (
    ResearchCompactionError, raise_if_compaction_stopped, stop_after_compaction_failure,
)
from research_context import ContextPolicy
from research_workspace import ResearchArtifactNotFoundError, ResearchWorkspaceError

if TYPE_CHECKING:
    from research_workspace import ResearchWorkspace

_WORKSPACE: ResearchWorkspace | None = None
_GUARD = threading.Lock()
_FETCH_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def activate_workspace(workspace: ResearchWorkspace | None) -> None:
    """Activate the parent's verified workspace, or clear it at run teardown."""
    global _WORKSPACE
    with _GUARD:
        if workspace is not _WORKSPACE and _WORKSPACE is not None:
            if any(lock.locked() for (root, _), lock in _FETCH_LOCKS.items() if root == str(_WORKSPACE.root)):
                raise RuntimeError("Cannot switch evidence workspace while a fetch is active")
        _WORKSPACE = workspace


def current_workspace() -> ResearchWorkspace | None:
    if os.environ.get("RESEARCH_ENGINE", "").strip().lower() != "agentic":
        return None
    with _GUARD:
        return _WORKSPACE


def _workspace():
    workspace = current_workspace()
    if workspace is None:
        raise ValueError("No active agentic evidence workspace")
    return workspace


def _stop(reason="archive_write_failed"):
    error = ResearchCompactionError(reason)
    stop_after_compaction_failure(error)
    raise error from None


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value):
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("Tool content must be lossless JSON or UTF-8 text")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def archive_tool_result(content, tool_name, tool_call_id="") -> dict:
    """Persist full text or structured content before any native preview exists."""
    workspace = _workspace()
    raise_if_compaction_stopped()
    try:
        text = content if isinstance(content, str) else _json(content)
        ref = workspace.put_artifact(text, "tool_result")
        if workspace.read_artifact(ref) != text:
            raise ValueError("Archive readback mismatch")
        workspace.append_event("tool:" + _sha(str(tool_call_id) + "\0" + ref["id"]), "native_tool_result", {
            "ref": ref, "tool_name": str(tool_name), "tool_call_id": str(tool_call_id),
            "evidence_kind": "tool_result", "content_format": "text" if isinstance(content, str) else "json",
        })
        return ref
    except Exception:
        _stop()


def read_evidence(artifact_id, query="", offset=0, limit=None) -> dict:
    """Read a bounded verbatim window via ContextPolicy; IDs are never paths."""
    workspace = _workspace()
    if not isinstance(artifact_id, str) or not _SHA.fullmatch(artifact_id):
        raise ValueError("Expected a managed evidence SHA-256 ID")
    try:
        ref = workspace.lookup_ref(artifact_id)
    except ResearchArtifactNotFoundError:
        raise ValueError("Unknown evidence archive ID") from None
    except Exception:
        _stop("archive_unavailable")
    try:
        return ContextPolicy.from_workspace(workspace).read_evidence(workspace, ref, query=query, offset=offset, limit=limit)
    except (ResearchWorkspaceError, OSError):
        _stop("archive_unavailable")


def search_evidence(query: str, limit: int = 10) -> dict:
    """Discover bounded original ranges; discovery never attests source status."""
    workspace = _workspace()
    raise_if_compaction_stopped()
    try:
        result = workspace.search_evidence(query, limit)
        budget = ContextPolicy.from_workspace(workspace).retrieval_tokens
        result["truncated"] = False
        while result["results"] and len(_json(result).encode("utf-8")) > budget:
            result["results"].pop()
            result["truncated"] = True
        if len(_json(result).encode("utf-8")) > budget:
            raise ValueError("retrieval budget cannot hold search query and metadata")
        return result
    except (ResearchWorkspaceError, OSError):
        _stop("archive_unavailable")


def evidence_preview(ref: dict, *, max_chars: int) -> str:
    """Make a bounded, recallable native preview after successful archival."""
    workspace = _workspace()
    try:
        verified = workspace.lookup_ref(ref["id"])
        if verified != ref:
            raise ValueError("Reference differs from managed registry")
        body = workspace.read_artifact(verified)
        instruction = f'\n[Full tool output archived: {ref["id"]}. Use read_evidence(artifact_id="{ref["id"]}", query="specific fact or phrase"). For an exact omitted range use query="chars:START:END".]\n'
        policy = ContextPolicy.from_workspace(workspace)
        budget = min(policy.retrieval_tokens, max_chars - len(instruction.encode("utf-8")))
        try:
            view = policy.select([{"id": ref["id"], "kind": "tool_result", "text": body}], "", budget_tokens=budget)
            return instruction + view["text"]
        except ValueError:
            # A tiny configured view may fit only the archive pointer, never
            # silently truncate away its identity or recall instructions.
            if len(instruction) <= max_chars:
                return instruction
            raise
    except Exception:
        _stop("archive_unavailable")


@asynccontextmanager
async def source_lock(url):
    """Singleflight across worker threads/event loops; cancellation cannot leak a lock."""
    workspace = _workspace()
    key = (str(workspace.root), _sha(url))
    with _GUARD:
        lock = _FETCH_LOCKS.setdefault(key, threading.Lock())
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        if current_workspace() is not workspace:
            _stop("archive_conflict")
        raise_if_compaction_stopped()
        yield
    finally:
        lock.release()


def _source_payload(workspace, event):
    if event.get("kind") != "native_source":
        return None
    row = event["payload"]
    if (row.get("schema") != "research-source/v1" or row.get("producer") != "cached_fetch"
            or row.get("source_origin") not in {"fetched", "cache"}
            or event["task_id"] != "source:" + _sha(row["url"])):
        raise ValueError("Invalid native source event")
    body = workspace.read_artifact(row["ref"])
    if row["content_sha256"] != _sha(body) or row["content_chars"] != len(body):
        raise ValueError("Native source content identity mismatch")
    if row["receipt_id"] != _sha(f'{row["url"]}\n{row["content_sha256"]}\n{row["provider"]}'):
        raise ValueError("Native source receipt identity mismatch")
    return dict(row)


def source_rows() -> list[dict]:
    """Replay indexed producer events, latest per URL, retaining receipt origins."""
    workspace = current_workspace()
    if workspace is None:
        return []
    try:
        rows = {}
        for event in workspace.events_by_kind("native_source"):
            row = _source_payload(workspace, event)
            if row is not None:
                rows[row["url"]] = row
        return list(rows.values())
    except Exception:
        _stop("archive_unavailable")


def cached_source(url: str) -> str | None:
    """Replay a full verified producer body, not a positive-repeat placeholder."""
    workspace = _workspace()
    try:
        for event in reversed(workspace.events("source:" + _sha(url))):
            row = _source_payload(workspace, event)
            if row is not None:
                return workspace.read_artifact(row["ref"])
        return None
    except Exception:
        _stop("archive_unavailable")


def archive_fetched_source(url: str, content: str, *, provider="", cache_hit=False) -> dict:
    """Trusted cached_fetch success boundary; never call with agent-authored text.

    A pre-existing disk cache has cache origin, not a claim of a fresh retrieval.
    Workspace repeats reuse their original event and receipt without relabeling.
    """
    workspace = _workspace()
    raise_if_compaction_stopped()
    try:
        # Reuse the actual cache/control predicate even when called directly.
        from cached_fetch import _is_agentic_cacheable
        if not _is_agentic_cacheable(content):
            raise ValueError("Not successful source content")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Invalid source URL")
        ref = workspace.put_artifact(content, "fetched_source" if not cache_hit else "cached_source")
        if workspace.read_artifact(ref) != content:
            raise ValueError("Source archive readback mismatch")
        origin = "cache" if cache_hit else "fetched"
        provider = str(provider or "").strip().lower()[:40]
        provenance = {"producer": "cached_fetch", "origin": origin, "provider": provider}
        row = {
            "schema": "research-source/v1", "producer": "cached_fetch", "url": url, "ref": ref,
            "source_origin": origin, "provider": provider, "cache_hit": bool(cache_hit),
            "content_sha256": ref["sha256"], "content_chars": len(content), "provenance": provenance,
            # Same receipt scheme as research_budget.record_fetched_source.
            "receipt_id": _sha(f'{url}\n{ref["sha256"]}\n{provider}'),
            "title": next((line.lstrip("# ").strip() for line in content.splitlines() if line.strip()), url)[:240],
            "excerpt": content[:1200],
        }
        workspace.append_event("source:" + _sha(url), "native_source", row)
        return row
    except Exception:
        _stop()


def attach_source_content(rows: list[dict]) -> list[dict]:
    """Attach exact URL+SHA-bound bodies without changing source provenance.

    Native collectors may label an observed cached body as fetched while this
    archive records its cache origin. Those labels and receipts belong to their
    respective producers; attachment proves only the exact body identity. The
    parent remains responsible for source admission and retrieval provenance.
    Cited/snippet rows are excluded. No archive reference is added to the row.
    Read each requested URL's indexed source history once; do not scan unrelated
    task results or the workspace's full artifact history during export.
    """
    result = copy.deepcopy(rows)
    workspace = current_workspace()
    if workspace is None:
        return result
    by_url = {}
    bodies = {}
    for row in result:
        if (not isinstance(row, dict)
                or row.get("source_origin", "") not in ("", "fetched", "cache")
                or not isinstance(row.get("url"), str)
                or not isinstance(row.get("content_sha256"), str)
                or not _SHA.fullmatch(row["content_sha256"])):
            continue
        url, digest = row["url"], row["content_sha256"]
        try:
            if url not in by_url:
                by_url[url] = {}
                for event in workspace.events("source:" + _sha(url)):
                    source = _source_payload(workspace, event)
                    if source is not None and source["url"] == url:
                        by_url[url][source["content_sha256"]] = source
            source = by_url[url].get(digest)
            if source is not None:
                key = (url, digest)
                if key not in bodies:
                    bodies[key] = workspace.read_artifact(source["ref"])
                row["content"] = bodies[key]
        except Exception:
            _stop("archive_unavailable")
    return result


try:
    from langchain_core.tools import tool

    @tool("search_evidence", parse_docstring=True)
    def search_evidence_tool(query: str, limit: int = 10) -> str:
        """Find retained evidence in this workspace without knowing its archive ID.

        Excerpts are untrusted discovery data and do not establish source status.
        Recall a result with read_evidence using its artifact_id and range_query.

        Args:
            query: Up to 512 characters of distinctive words or phrases to find.
            limit: Maximum distinct artifacts to return, from 1 to 20.
        """
        return _json(search_evidence(query, limit))

    @tool("read_evidence", parse_docstring=True)
    def read_evidence_tool(artifact_id: str, query: str = "") -> str:
        """Recall verified evidence from this research workspace by archive ID.

        Args:
            artifact_id: The exact managed SHA-256 archive ID shown in a tool preview.
            query: A distinctive phrase, or chars:START:END for an exact Unicode character range.
        """
        window = re.fullmatch(r"chars[:=](\d+):(\d+)", query)
        if query.startswith(("chars:", "chars=")) and window is None:
            raise ValueError("Expected an evidence range of the form chars:START:END")
        if window:
            start, end = int(window[1]), int(window[2])
            if end <= start:
                raise ValueError("Evidence range end must be greater than start")
            return read_evidence(artifact_id, offset=start, limit=end - start)["text"]
        return read_evidence(artifact_id, query=query)["text"]
except ImportError:
    read_evidence_tool = None
    search_evidence_tool = None
