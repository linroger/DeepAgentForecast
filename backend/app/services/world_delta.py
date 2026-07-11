"""Inter-round world-delta digest for the calendar-temporal simulation (spec §4).

Pure, deterministic, no LLM, no I/O. Each round the loop hands the digest built here
to the NEXT round's world-clock header ("## WHAT CHANGED LAST PERIOD"). It surfaces
only qualitative facts: the fired scheduled events, the most influential organic
posts, and — optionally — a direction-only momentum line for the leading scenario.

Herding guard (judge-vetoed): **no numeric shares or percentages ever appear** in
agent-facing text. The full quantitative shares live in ``world_digest.jsonl`` and
the trajectory artifacts, never here. The momentum line is strictly qualitative
("strengthened|weakened|held") regardless of what extra keys ``leader_move`` carries.
"""

from __future__ import annotations

from typing import Optional

_DIRECTION_WORD = {"up": "strengthened", "down": "weakened", "flat": "held"}


def build_world_delta(round_actions: list, fired_events: list,
                      leader_move: Optional[dict] = None,
                      top_k: int = 5, char_cap: int = 900) -> str:
    """Build the qualitative delta digest for one completed period.

    Sections in order: fired events as ``[{date}] {content}`` lines; the top-k
    organic posts ranked by author ``influence_weight`` as
    ``{actor_name}: {content[:140]}``; if ``leader_move`` is given
    (``{"leader": str, "direction": "up"|"down"|"flat"}``), a qualitative momentum
    line — never any numeric share. Output is truncated to ``char_cap`` characters.
    Any error or fully empty input returns ``""`` (the header then renders its own
    placeholder) — this function must never break a running round.
    """
    try:
        lines: list = []

        # --- fired scheduled events this period ---------------------------------
        for ev in fired_events or []:
            if not isinstance(ev, dict):
                continue
            content = str(ev.get("content", "") or "").strip()
            if not content:
                continue
            date = str(ev.get("date", "") or "").strip()
            if date and not content.startswith(f"[{date}]"):
                lines.append(f"[{date}] {content}")
            else:  # content already carries the "[{date}] " prefix (config-gen §4)
                lines.append(content)

        # --- top-k organic posts by author influence_weight ----------------------
        posts = []
        for idx, act in enumerate(round_actions or []):
            if not isinstance(act, dict):
                continue
            content = str(act.get("content", "") or "").strip()
            if not content:
                continue
            try:
                weight = float(act.get("influence_weight", 0.0) or 0.0)
            except (TypeError, ValueError):
                weight = 0.0
            name = str(act.get("actor_name") or act.get("agent_name")
                       or act.get("name") or "unknown").strip() or "unknown"
            posts.append((-weight, idx, name, content))
        posts.sort()  # weight desc, then input order — deterministic tie-break
        for _, _, name, content in posts[: max(0, int(top_k))]:
            lines.append(f"{name}: {content[:140]}")

        # --- qualitative momentum (direction only; no shares/percentages ever) ---
        if isinstance(leader_move, dict):
            leader = str(leader_move.get("leader", "") or "").strip()
            word = _DIRECTION_WORD.get(str(leader_move.get("direction", "") or ""))
            if leader and word:
                lines.append(f"Momentum: {leader} {word} this period.")

        if not lines:
            return ""
        return "\n".join(lines)[: max(0, int(char_cap))]
    except Exception:
        return ""
