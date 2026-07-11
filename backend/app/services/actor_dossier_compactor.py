"""Render a bounded, canonical actor dossier for graph extraction.

Parallel research tracks retain their raw dossiers for audit. The graph stage,
however, should ingest one deduplicated view centred on the canonical cast and
its one-hop relationships, rather than a concatenation of every track's prose.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..utils.actors import entity_simulation_tier, normalize_name, salience_score


def _structured_text(value: Any) -> str:
    """Render nested JSON values without leaking Python container reprs."""
    if value is None:
        return ""
    if isinstance(value, dict):
        parts: List[str] = []
        for key, nested in value.items():
            rendered = _structured_text(nested)
            if rendered:
                label = str(key).replace("_", " ").strip().title()
                parts.append(f"{label}: {rendered}")
        return "; ".join(parts)
    if isinstance(value, set):
        value = sorted(value, key=lambda item: str(item))
    if isinstance(value, (list, tuple)):
        return "; ".join(
            rendered for item in value
            if (rendered := _structured_text(item))
        )
    return str(value)


def _text(value: Any, limit: int = 320) -> str:
    value = " ".join(_structured_text(value).split()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _items(value: Any, limit: int = 4, item_chars: int = 220) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in value:
        rendered = _text(item, item_chars)
        key = rendered.casefold()
        if rendered and key not in seen:
            seen.add(key)
            out.append(rendered)
        if len(out) >= limit:
            break
    return out


def _actor_rank(actor: Dict[str, Any]) -> Tuple[int, float, str]:
    return (
        entity_simulation_tier(actor),
        -salience_score(actor),
        normalize_name(actor.get("name")) or str(actor.get("name") or "").casefold(),
    )


def _relation_rank(row: Dict[str, Any], cast: set[str]) -> Tuple[int, int, str, str, str]:
    source = normalize_name(row.get("source") or row.get("from"))
    target = normalize_name(row.get("target") or row.get("to"))
    strength = str(row.get("strength") or "").casefold()
    grade = str(row.get("grade") or "").upper()
    return (
        0 if source in cast and target in cast else 1,
        {"high": 0, "medium": 1, "low": 2}.get(strength, 3),
        grade,
        source,
        target,
    )


def _render_situation_brief(value: Any) -> List[str]:
    """Render the actors.json situation-brief contract as readable Markdown."""
    if not value:
        return []
    if not isinstance(value, dict):
        brief = _text(value, 1800)
        return ["", "## Situation Brief", "", brief] if brief else []

    lines: List[str] = ["", "## Situation Brief"]
    rendered = False
    for label, key in (
        ("State of Play", "current_situation"),
        ("Context", "context"),
        ("Dynamics", "dynamics"),
    ):
        body = _text(value.get(key), 900)
        if body:
            lines.extend(["", f"### {label}", "", body])
            rendered = True
    for label, key in (
        ("Fault Lines", "fault_lines"),
        ("Catalysts", "catalysts"),
    ):
        rows = _items(value.get(key), limit=6, item_chars=360)
        if rows:
            lines.extend(["", f"### {label}"])
            lines.extend(f"- {row}" for row in rows)
            rendered = True
    return lines if rendered else []


def _render_incentives(value: Any, limit: int = 3) -> List[str]:
    """Render structured or legacy incentive values as bounded bullet rows."""
    if isinstance(value, dict):
        values: List[Any] = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = [value] if value else []
    rows: List[str] = []
    for incentive in values:
        if isinstance(incentive, dict):
            parts: List[str] = []
            for label, key, chars in (
                ("Driver", "driver", 180),
                ("Gains if", "gains_if", 220),
                ("Loses if", "loses_if", 220),
                ("Intensity", "intensity", 60),
            ):
                rendered = _text(incentive.get(key), chars)
                if rendered:
                    parts.append(f"**{label}:** {rendered}")
            # Forward-compatible fallback for a dict outside the known schema.
            row = "; ".join(parts) or _text(incentive, 520)
        else:
            row = _text(incentive, 520)
        if row:
            rows.append(f"- {row}")
        if len(rows) >= limit:
            break
    return (["", "**Incentives:**", *rows] if rows else [])


def render_compact_actor_dossier(
        actors: Any, *, max_chars: int = 80_000, max_actors: int = 20,
        max_relationships: int = 80, max_neighbors: int = 40) -> Tuple[str, Dict[str, Any]]:
    """Return ``(markdown, telemetry)`` for the canonical actor view.

    Only relationships touching a selected cast member are admitted. Non-cast
    endpoints are bounded one-hop context entities; unrelated character/entity
    sprawl is excluded before graph chunking.
    """
    if not isinstance(actors, dict):
        return "", {"fallback_reason": "actors_not_dict"}
    rows = [row for row in (actors.get("actors") or [])
            if isinstance(row, dict) and _text(row.get("name"), 160)]
    rows = sorted(rows, key=_actor_rank)[:max(1, int(max_actors))]
    if not rows:
        return "", {"fallback_reason": "no_actor_rows"}

    cast = {normalize_name(row.get("name")) for row in rows}
    cast.discard("")
    relations: List[Dict[str, Any]] = []
    seen_rel: set[Tuple[str, str, str]] = set()
    neighbor_names: set[str] = set()
    candidates = [row for row in (actors.get("relationships") or []) if isinstance(row, dict)]
    for row in sorted(candidates, key=lambda item: _relation_rank(item, cast)):
        source_raw = row.get("source") or row.get("from")
        target_raw = row.get("target") or row.get("to")
        source = normalize_name(source_raw)
        target = normalize_name(target_raw)
        if not source or not target or (source not in cast and target not in cast):
            continue
        relation = _text(row.get("type") or row.get("relation") or "RELATED_TO", 80).upper()
        key = (source, target, relation)
        if key in seen_rel:
            continue
        new_neighbors = {node for node in (source, target) if node not in cast}
        if len(neighbor_names | new_neighbors) > max_neighbors:
            continue
        neighbor_names.update(new_neighbors)
        seen_rel.add(key)
        relations.append(row)
        if len(relations) >= max_relationships:
            break

    lines: List[str] = ["# Canonical Actor Dossier"]
    question = _text(actors.get("central_question"), 900)
    as_of = _text(actors.get("as_of_date"), 40)
    if question:
        lines.extend(["", "## Forecast Question", "", question])
    if as_of:
        lines.extend(["", f"**Evidence as of:** {as_of}"])
    lines.extend(_render_situation_brief(actors.get("situation_brief")))

    lines.extend(["", "## Key Actors"])
    for actor in rows:
        name = _text(actor.get("name"), 160)
        role = _text(actor.get("role") or actor.get("description"), 520)
        lines.extend(["", f"### {name}"])
        summary_bits = [
            _text(actor.get("type"), 80),
            _text(actor.get("role_class"), 80),
            _text(actor.get("influence"), 80),
        ]
        summary = " · ".join(bit for bit in summary_bits if bit)
        if summary:
            lines.extend(["", f"**Profile:** {summary}"])
        if role:
            lines.extend(["", role])
        stance = _text(actor.get("stance") or actor.get("stated_vs_revealed"), 520)
        if stance:
            lines.extend(["", f"**Position:** {stance}"])
        for label, key in (
            ("Goals", "goals"), ("Constraints", "constraints"),
            ("Resources", "resources"), ("Vulnerabilities", "vulnerabilities"),
        ):
            values = _items(actor.get(key), limit=4)
            if values:
                lines.extend(["", f"**{label}:** " + "; ".join(values)])
        lines.extend(_render_incentives(actor.get("incentives")))

    lines.extend(["", "## Key One-Hop Relationships"])
    for row in relations:
        source = _text(row.get("source") or row.get("from"), 120)
        target = _text(row.get("target") or row.get("to"), 120)
        relation = _text(row.get("type") or row.get("relation") or "RELATED_TO", 80).upper()
        attributes = ", ".join(bit for bit in (
            _text(row.get("sign") or row.get("valence"), 50),
            _text(row.get("strength"), 50),
            _text(row.get("grade"), 50),
        ) if bit)
        basis = _text(row.get("basis"), 260)
        detail = f" ({attributes})" if attributes else ""
        if basis:
            detail += f" — {basis}"
        lines.append(f"- **{source} → {target}:** {relation}{detail}")

    events = [row for row in (actors.get("key_events") or []) if isinstance(row, dict)]
    events = sorted(events, key=lambda row: str(row.get("date") or row.get("when") or ""))[:30]
    if events:
        lines.extend(["", "## Decision-Relevant Events"])
        for row in events:
            date = _text(row.get("date") or row.get("when"), 50)
            event = _text(row.get("event") or row.get("description") or row.get("text"), 320)
            if event:
                lines.append(f"- **{date}:** {event}" if date else f"- {event}")

    topics = _items(actors.get("hot_topics"), limit=20, item_chars=180)
    if topics:
        lines.extend(["", "## Forecast-Relevant Topics", "", "; ".join(topics)])

    rendered_lines: List[str] = []
    used = 0
    for line in lines:
        addition = len(line) + (1 if rendered_lines else 0)
        if used + addition + 1 > max_chars:  # reserve the final newline
            break
        rendered_lines.append(line)
        used += addition
    markdown = "\n".join(rendered_lines).rstrip() + "\n"
    return markdown, {
        "actors_rendered": len(rows),
        "relationships_rendered": len(relations),
        "context_neighbors_rendered": len(neighbor_names),
        "compact_chars": len(markdown),
        "truncated": len(rendered_lines) < len(lines),
        "fallback_reason": None,
    }
