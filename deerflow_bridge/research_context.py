"""Offline, provenance-labelled working views over complete research artifacts.

This is selection, not summarization or a global memory. Callers retain originals
in their run workspace and inject only ``view['text']`` into a prompt. Ref lists
are control-plane metadata, not additional unbudgeted prompt text. A caller must
also budget its instructions, conversation, tool schemas and output reservation.

One UTF-8 byte counts as one estimated token. That is deliberately conservative
for byte-based tokenizers, not a tokenizer measurement: 64,000 characters is NOT
64,000 tokens (CJK commonly uses three UTF-8 bytes per character, emoji four).
No tokenizer, model, network, filesystem path resolution or service is used here.

Workspace protocol: ``put_artifact(text, kind)`` durably writes the
full string and returns ``{id, sha256, bytes, path}`` (id is the content SHA);
``read_artifact(ref)`` verifies the workspace scope and returns the exact string.
This module additionally checks SHA-256. Workspace errors propagate unchanged.
Artifact IDs alone are sufficient for select(), but archive/read use the complete
reference mapping. Character offsets are Unicode code points, end exclusive.

Native integration: archive observed tool text before creating a bounded view;
select task-relevant archived blocks when assembling each worker's prompt; offer
read_evidence as a scoped local tool for omitted ranges. Retain existing native
offloading, durable compaction receipts and caching. In particular, this module
cannot turn an agent answer or compaction summary into fetched-source evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import unicodedata
from typing import Any, ClassVar


ESTIMATION_BASIS = (
    "UTF-8 bytes, 1 byte = 1 estimated token; conservative offline content "
    "estimate, not a tokenizer measurement; includes text labels and omission "
    "notice, excludes ref metadata and caller-owned prompt/tool envelopes"
)
_HEADER = "Verbatim artifact excerpts; producer kind labels do not verify source retrieval.\n"
_STOPWORDS = frozenset("a an and are as at be by for from how in is it of on or that the this to was what when which with".split())
_CONTRADICTION = re.compile(r"\b(?:not|no|however|but|contradict\w*|revis\w*|disput\w*|declin\w*|versus|instead|uncertain\w*)\b|矛盾|然而|修订|下降|低于|否认|并非", re.I)
_DATE = re.compile(r"\b(?:19|20)\d{2}(?:[-/]\d{1,2}){0,2}\b|\d{4}年")


def estimate_tokens(text: str) -> int:
    """Conservative text-only estimate, including multibyte Unicode content."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return len(text.encode("utf-8"))


def _integer(value: Any, name: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be a nonempty, single-line string")
    return value


def _terms(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms: set[str] = set()
    for term in re.findall(r"[a-z][a-z0-9_]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+", normalized):
        if "\u3400" <= term[0] <= "\u9fff":
            terms.update(term[i:i + 2] for i in range(max(1, len(term) - 1)))
        elif term not in _STOPWORDS:
            terms.add(term)
    return terms


def _rank(excerpt: str, query_terms: set[str]) -> tuple[int, int]:
    matched = query_terms.intersection(_terms(excerpt))
    single_cjk = {term for term in query_terms if len(term) == 1 and "\u3400" <= term <= "\u9fff"}
    if single_cjk:
        normalized = unicodedata.normalize("NFKC", excerpt)
        matched.update(term for term in single_cjk if term in normalized)
    relevance = len(matched)
    salience = (
        4 * bool(_CONTRADICTION.search(excerpt))
        + 2 * bool(_DATE.search(excerpt))
        + bool(re.search(r"\d", excerpt))
    )
    return -relevance, -salience


def _prefix_end(text: str, start: int, end: int, byte_budget: int) -> int:
    """Largest code-point boundary fitting the byte budget (no broken UTF-8)."""
    low, high = start, min(end, start + max(0, byte_budget))
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[start:middle]) <= byte_budget:
            low = middle
        else:
            high = middle - 1
    return low


def _spans(text: str, byte_budget: int):
    """Visit all paragraphs, splitting oversized ones without dropping bytes."""
    start = 0
    boundaries = [m.end() for m in re.finditer(r"\n[ \t\r]*\n+", text)]
    if not boundaries or boundaries[-1] != len(text):
        boundaries.append(len(text))
    for paragraph_end in boundaries:
        while start < paragraph_end:
            end = _prefix_end(text, start, paragraph_end, byte_budget)
            if end == start:
                return
            if end < paragraph_end:
                # Prefer a nearby word/sentence boundary; every separator is
                # retained in one excerpt, so windows can reconstruct originals.
                floor = start + (end - start) * 3 // 4
                for at in range(end - 1, floor - 1, -1):
                    if text[at].isspace() or text[at] in "。！？":
                        end = at + 1
                        break
            yield start, end
            start = end


def _label(block: dict, start: int, end: int) -> str:
    fields = {"ref": block["id"], "kind": block["kind"], "chars": [start, end]}
    if block.get("source_url"):
        fields["source_url"] = block["source_url"]
    if block["kind"] not in {"fetched_source", "search_result", "tool_result"}:
        fields["status"] = "derived or unverified; not fetched evidence"
    return "\n[" + json.dumps(fields, ensure_ascii=False, separators=(",", ":")) + "]\n"


def _reference(block: dict, start: int, end: int) -> dict:
    result = {"ref": block["id"], "kind": block["kind"], "start": start, "end": end}
    if block.get("source_url"):
        result["source_url"] = block["source_url"]
    return result


def _omission_notice(first: dict, count: int) -> str:
    """Keep at least one actionable omitted range in the budgeted prompt text."""
    identity = json.dumps(first["ref"], ensure_ascii=False)
    return (
        f'\n[Omitted evidence: ref={identity} chars={first["start"]}:{first["end"]}; '
        f"{count} range(s) total. Use read_evidence with this ref/character range; "
        "complete list in omitted_refs.]\n"
    )


def _omission_budget(blocks: list[dict]) -> int:
    # Every disjoint range contains at least one code point; this count and the
    # maximum endpoints conservatively bound the later, actual notice length.
    count_bound = sum(len(block["text"]) for block in blocks)
    return max(
        estimate_tokens(_omission_notice(_reference(block, len(block["text"]), len(block["text"])), count_bound))
        for block in blocks
    )


def _gaps(block: dict, spans: list[tuple[int, int]]) -> list[dict]:
    result, cursor = [], 0
    for start, end in sorted(spans):
        if start > cursor:
            result.append(_reference(block, cursor, start))
        cursor = end
    if cursor < len(block["text"]):
        result.append(_reference(block, cursor, len(block["text"])))
    return result


def _validate_ref(ref: Any) -> dict:
    if not isinstance(ref, dict):
        raise ValueError("artifact ref must include identity, bytes, path and SHA-256")
    digest = ref.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("artifact ref must include a valid SHA-256")
    if (ref.get("id") != digest or type(ref.get("bytes")) is not int
            or ref["bytes"] < 0 or ref.get("path") != f"blobs/{digest}.txt"):
        raise ValueError("artifact ref identity, size or path is invalid")
    # Do not resolve caller-supplied paths. Scope/path verification belongs to
    # the durable workspace, before it opens a reference.
    return {key: ref[key] for key in ("id", "sha256", "bytes", "path")}


@dataclass(frozen=True)
class ContextPolicy:
    """Validated per-worker working envelope, independent of the linear engine."""

    context_window_tokens: int = 128_000
    working_tokens: int = 64_000
    reserved_output_tokens: int = 16_000
    prompt_overhead_tokens: int = 16_000
    safety_margin_tokens: int = 8_000
    retrieval_tokens: int = 8_000
    paragraph_tokens: int = 2_048

    ENV_FIELDS: ClassVar[dict[str, str]] = {
        "RESEARCH_AGENTIC_CONTEXT_WINDOW_TOKENS": "context_window_tokens",
        "RESEARCH_AGENTIC_WORKING_TOKENS": "working_tokens",
        "RESEARCH_AGENTIC_RESERVED_OUTPUT_TOKENS": "reserved_output_tokens",
        "RESEARCH_AGENTIC_PROMPT_OVERHEAD_TOKENS": "prompt_overhead_tokens",
        "RESEARCH_AGENTIC_SAFETY_MARGIN_TOKENS": "safety_margin_tokens",
        "RESEARCH_AGENTIC_RETRIEVAL_TOKENS": "retrieval_tokens",
        "RESEARCH_AGENTIC_PARAGRAPH_TOKENS": "paragraph_tokens",
    }

    def __post_init__(self):
        for name in self.ENV_FIELDS.values():
            _integer(getattr(self, name), name)
        if self.working_tokens + self.reserved_output_tokens + self.prompt_overhead_tokens + self.safety_margin_tokens > self.context_window_tokens:
            raise ValueError("working budget and reserved margins exceed declared model window")
        if self.retrieval_tokens > self.working_tokens:
            raise ValueError("retrieval_tokens must fit the working budget")
        if self.paragraph_tokens < 4:
            raise ValueError("paragraph_tokens must hold at least one UTF-8 code point")

    @classmethod
    def from_env(cls) -> ContextPolicy:
        """Read strictly positive decimal RESEARCH_AGENTIC_* token settings.

        Oversized budgets fail instead of silently changing the requested policy.
        The declared model window is configuration, not model capability discovery.
        """
        settings = {}
        for name, field in cls.ENV_FIELDS.items():
            value = os.environ.get(name)
            if value is not None:
                if not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
                    raise ValueError(f"{name} must be a positive decimal integer")
                settings[field] = int(value)
        return cls(**settings)

    def to_dict(self) -> dict:
        """Stable JSON-safe policy identity for durable task-input bindings."""
        return {
            "schema_version": "research-context-policy/v1",
            "estimation_basis": ESTIMATION_BASIS,
            **{field: getattr(self, field) for field in self.ENV_FIELDS.values()},
        }

    def select(self, blocks: list[dict], query: str, budget_tokens: int | None = None) -> dict:
        """Select verbatim relevant paragraphs throughout all supplied originals.

        Oversized paragraphs become labelled excerpts, never stored truncations.
        Lexical matches rank first; contradictions, dates and numeric statements
        break ties. This deterministic heuristic cannot establish semantic recall.
        Omitted ranges identify exactly what remains available for follow-up.
        """
        budget = self.working_tokens if budget_tokens is None else _integer(budget_tokens, "budget_tokens")
        if budget > self.working_tokens:
            raise ValueError("budget_tokens exceeds policy working envelope")
        if not isinstance(blocks, list) or not isinstance(query, str):
            raise ValueError("blocks must be a list and query must be a string")
        seen = set()
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                raise ValueError("each block requires id, text and kind")
            identity = _identity(block.get("id"), "block id")
            _identity(block.get("kind"), "block kind")
            if "source_url" in block and block["source_url"] is not None:
                _identity(block["source_url"], "source_url")
            if identity in seen:
                raise ValueError("duplicate block id makes evidence ranges ambiguous")
            seen.add(identity)
        if not blocks or not any(block["text"] for block in blocks):
            return self._view("", [], [], budget)
        # Do not evict evidence to reserve an omission notice if one full,
        # labelled excerpt per document already fits without any omissions.
        complete_parts, complete_refs = [], []
        complete_cost = estimate_tokens(_HEADER)
        for block in blocks:
            if not block["text"]:
                continue
            label = _label(block, 0, len(block["text"]))
            complete_cost += estimate_tokens(label) + estimate_tokens(block["text"])
            if complete_cost > budget:
                break
            complete_parts.append(label + block["text"])
            complete_refs.append(_reference(block, 0, len(block["text"])))
        else:
            return self._view(_HEADER + "".join(complete_parts), complete_refs, [], budget)
        base_cost = estimate_tokens(_HEADER) + _omission_budget(blocks)
        if budget <= base_cost:
            raise ValueError("budget_tokens cannot hold provenance and omission notice")
        query_terms = _terms(query)
        candidates = []
        for index, block in enumerate(blocks):
            text = block["text"]
            label_cost = estimate_tokens(_label(block, len(text), len(text)))
            cap = min(self.paragraph_tokens, budget - base_cost - label_cost)
            if cap < 4:
                continue
            for start, end in _spans(text, cap):
                excerpt = text[start:end]
                candidates.append((*_rank(excerpt, query_terms), index, start, end))
        remaining = budget - base_cost
        selected = []
        for _, _, index, start, end in sorted(candidates):
            block = blocks[index]
            rendered = _label(block, start, end) + block["text"][start:end]
            cost = estimate_tokens(rendered)
            if cost <= remaining:
                selected.append((index, start, end, rendered))
                remaining -= cost
        selected.sort(key=lambda item: (item[0], item[1]))
        selected_refs = [_reference(blocks[i], start, end) for i, start, end, _ in selected]
        omitted_refs = []
        for index, block in enumerate(blocks):
            omitted_refs.extend(_gaps(block, [(s, e) for i, s, e, _ in selected if i == index]))
        text = _HEADER + "".join(part for _, _, _, part in selected)
        if omitted_refs:
            text += _omission_notice(omitted_refs[0], len(omitted_refs))
        return self._view(text, selected_refs, omitted_refs, budget)

    @staticmethod
    def _view(text: str, selected: list[dict], omitted: list[dict], budget: int) -> dict:
        return {
            "text": text, "selected_refs": selected, "omitted_refs": omitted,
            "estimated_tokens": estimate_tokens(text), "budget_tokens": budget,
            "estimation_basis": ESTIMATION_BASIS,
        }

    def archive_result(self, workspace, text: str, *, kind: str, query: str) -> dict:
        """Commit the entire result before making a working view; never swallow IO errors."""
        if not isinstance(text, str) or not isinstance(query, str):
            raise ValueError("text and query must be strings")
        _identity(kind, "kind")
        ref = _validate_ref(workspace.put_artifact(text, kind))
        if ref["sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest() or ref["bytes"] != estimate_tokens(text):
            raise ValueError("artifact integrity mismatch after archival")
        view = self.select([{"id": ref["id"], "text": text, "kind": kind}], query)
        for item in view["selected_refs"] + view["omitted_refs"]:
            item["ref"] = dict(ref)
        return {"ref": dict(ref), "view": view, "kind": kind, "query": query,
                "total_chars": len(text), "total_bytes": estimate_tokens(text)}

    def read_evidence(self, workspace, ref: dict, *, query: str = "", offset: int = 0, limit: int | None = None) -> dict:
        """Read a contiguous, bounded verbatim window from a verified artifact.

        Offset and limit count Unicode code points, not bytes or tokens, and
        define the candidate range. The token envelope can shorten the result.
        A query starts the window at the best matching paragraph in that range;
        skipped ranges remain in omitted_refs. Without a match it starts at the
        requested offset. next_offset follows the returned window, or is None
        at EOF; it does not imply all earlier ranges were included. Blank-query
        sequential paging omits no evidence. Only ``text`` is budgeted prompt
        content; ``excerpt`` and refs are for control-plane consumers.
        """
        ref = _validate_ref(ref)
        _integer(offset, "offset", minimum=0)
        if limit is not None:
            _integer(limit, "limit")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        text = workspace.read_artifact(ref)
        if (not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != ref["sha256"]
                or estimate_tokens(text) != ref["bytes"]):
            raise ValueError("artifact integrity check failed")
        if offset > len(text):
            raise ValueError("offset exceeds artifact length")
        # Content references authenticate bytes, not caller-added provenance.
        # archive_result retains the producer kind in its view/outer envelope;
        # this standalone read cannot recover that kind from a byte hash alone.
        block = {"id": ref["id"], "kind": "archived_artifact", "text": text}
        bound = len(text) if limit is None else min(len(text), offset + limit)
        overhead = estimate_tokens(_HEADER + _label(block, bound, bound)) + _omission_budget([block])
        available = self.retrieval_tokens - overhead
        if available < 4:
            raise ValueError("retrieval budget cannot hold provenance and one UTF-8 code point")
        requested_offset = offset
        terms = _terms(query)
        if terms:
            region = text[offset:bound]
            best = (0, 0, 0)
            for start, end in _spans(region, min(self.paragraph_tokens, available)):
                rank = (*_rank(region[start:end], terms), start)
                if rank[0] < 0 and rank < best:
                    best = rank
            offset += best[2]
        end = _prefix_end(text, offset, bound, available)
        spans = [(offset, end)] if end > offset else []
        selected = [_reference(block, offset, end)] if spans else []
        omitted = _gaps(block, spans)
        excerpt = text[offset:end]
        rendered = _HEADER + (_label(block, offset, end) + excerpt if spans else "")
        if omitted:
            rendered += _omission_notice(omitted[0], len(omitted))
        view = self._view(rendered, selected, omitted, self.retrieval_tokens)
        for item in selected + omitted:
            item["ref"] = dict(ref)
        return {**view, "ref": dict(ref), "excerpt": excerpt, "offset": offset,
                "requested_offset": requested_offset,
                "end_offset": end, "next_offset": end if end < len(text) else None,
                "total_chars": len(text)}


def archive_result(workspace, text: str, *, kind: str, query: str) -> dict:
    """Use a freshly validated environment policy to archive a complete result."""
    return ContextPolicy.from_env().archive_result(workspace, text, kind=kind, query=query)


def read_evidence(workspace, ref: dict, *, query: str = "", offset: int = 0, limit: int | None = None) -> dict:
    """Use a freshly validated environment policy to retrieve a local window."""
    return ContextPolicy.from_env().read_evidence(workspace, ref, query=query, offset=offset, limit=limit)
