"""LLM-assisted entity resolution / canonical-alias merge after graph build
(EXECPLAN2 I-1-4).

Graphiti's add_episode dedups by name+embedding *during* ingestion, but the same
real entity surfacing under different surface forms across research prose, seeded
actors, and simulation feedback ('OpenAI' / 'OpenAI 公司' / 'OpenAI, Inc.' /
'@OpenAI') can still produce duplicate nodes — splitting search recall, under-
counting centrality, and spawning personas for phantom nodes. This pass clusters
near-duplicate entity nodes (reusing actors.normalize_name + bidirectional
containment AND an embedding-cosine gate) and merges non-canonical surface forms
INTO the actors.json canonical name, then emits an audit (entity_merges.json).

OPTIONAL-DEGRADE: only runs when Config.GRAPH_RESOLVE_ENTITIES is true (default
off → never invoked → graph is byte-identical to today).

Over-merge guardrails (the spec's "Medium-High" risk): merge only WITHIN the same
primary label; require BOTH a normalized-name match (exact or containment) AND
embedding cosine ≥ threshold; never merge two actors.json canonicals into each
other; always pick the canonical as survivor; log every merge for audit/rollback.

The planning logic (clustering, survivor selection) is pure / side-effect-free so
it is unit-tested offline; only execute_merges touches the graph (best-effort,
per-merge isolated, serialized on the runtime's per-graph write lock).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from ..utils.actors import extract_actor_rows, normalize_name

logger = logging.getLogger("mirofish.entity_resolver")


# ----------------------------------------------------------------- pure helpers
def canonical_norm_set(actors: Any) -> set:
    """Normalized names of the researched actors.json canonicals."""
    return {normalize_name(r.get("name", "")) for r in extract_actor_rows(actors)
            if normalize_name(r.get("name", ""))}


def _cosine(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
    """Cosine similarity, or None if either vector is missing/empty."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


def _name_match(norm_a: str, norm_b: str) -> bool:
    """Exact normalized match, or bidirectional containment with ≥2-char overlap
    (mirrors actors.match_actor — avoids 'AI'-style short-name noise)."""
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    shorter = norm_a if len(norm_a) <= len(norm_b) else norm_b
    longer = norm_b if shorter is norm_a else norm_a
    return len(shorter) >= 2 and shorter in longer


def _primary_label(node: Dict[str, Any]) -> str:
    labels = [str(x) for x in (node.get("labels") or []) if x and str(x) != "Entity"]
    return labels[0] if labels else "Entity"


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def plan_merges(nodes: List[Dict[str, Any]], embeddings: Optional[List[List[float]]],
                canonical_norms: set, threshold: float) -> List[Dict[str, Any]]:
    """Pure: cluster near-duplicate nodes and choose survivors. No graph access.

    Returns [{survivor_uuid, survivor_name, primary_label, victims:[{uuid,name,similarity}]}].
    A pair is mergeable iff: same primary label AND name-match (exact/containment)
    AND (embedding cosine ≥ threshold when both embeddings exist, else exact-norm
    match as a safe fallback) AND not both canonical. Components with ≥2 canonicals
    are split so each canonical anchors its own cluster (never merge two canonicals).
    """
    n = len(nodes)
    if n < 2:
        return []
    emb = embeddings if (embeddings and len(embeddings) == n) else [None] * n
    norms = [normalize_name(nd.get("name", "")) for nd in nodes]
    labels = [_primary_label(nd) for nd in nodes]
    is_canon = [norms[i] in canonical_norms and bool(norms[i]) for i in range(n)]

    uf = _UnionFind(n)
    sim_cache: Dict[tuple, float] = {}
    for i in range(n):
        if not norms[i]:
            continue
        for j in range(i + 1, n):
            if not norms[j] or labels[i] != labels[j]:
                continue
            if is_canon[i] and is_canon[j] and norms[i] != norms[j]:
                continue  # never merge two DISTINCT canonicals (same-canonical dups are fine)
            if not _name_match(norms[i], norms[j]):
                continue
            cos = _cosine(emb[i], emb[j])
            if cos is None:
                # no embeddings → safe fallback: require EXACT normalized match
                if norms[i] != norms[j]:
                    continue
                cos = 1.0
            elif cos < threshold:
                continue
            uf.union(i, j)
            sim_cache[(i, j)] = round(cos, 4)

    # group component members
    comps: Dict[int, List[int]] = {}
    for i in range(n):
        comps.setdefault(uf.find(i), []).append(i)

    def _sim(a: int, b: int) -> float:
        return sim_cache.get((min(a, b), max(a, b)), 1.0)

    out: List[Dict[str, Any]] = []
    for members in comps.values():
        if len(members) < 2:
            continue
        # DISTINCT canonical names within one component → split so each canonical
        # anchors its own cluster (duplicates of the SAME canonical stay together).
        distinct_canon = {norms[m] for m in members if is_canon[m]}
        if len(distinct_canon) >= 2:
            canon_members = [m for m in members if is_canon[m]]
            groups: Dict[str, List[int]] = {}
            for m in members:
                if is_canon[m]:
                    groups.setdefault(norms[m], []).append(m)
            for m in members:
                if is_canon[m]:
                    continue
                best_c = max(canon_members, key=lambda c: _sim(c, m))
                groups[norms[best_c]].append(m)
            for grp in groups.values():
                if len(grp) >= 2:
                    survivor = _pick_survivor(grp, is_canon, norms)
                    out.append(_make_cluster(nodes, survivor,
                                             [m for m in grp if m != survivor], _sim))
        else:
            survivor = _pick_survivor(members, is_canon, norms)
            out.append(_make_cluster(nodes, survivor,
                                     [m for m in members if m != survivor], _sim))
    return out


def _pick_survivor(members: List[int], is_canon: List[bool], norms: List[str]) -> int:
    """Prefer a canonical member (longest among them); else the longest normalized
    name. Tie-break by the name string so planning is deterministic."""
    canon = [m for m in members if is_canon[m]]
    pool = canon if canon else members
    return min(pool, key=lambda m: (-len(norms[m]), norms[m]))


def _make_cluster(nodes, survivor: int, victims: List[int], sim_fn) -> Dict[str, Any]:
    return {
        "survivor_uuid": nodes[survivor]["uuid"],
        "survivor_name": nodes[survivor]["name"],
        "primary_label": _primary_label(nodes[survivor]),
        "victims": [
            {"uuid": nodes[v]["uuid"], "name": nodes[v]["name"],
             "similarity": sim_fn(survivor, v)}
            for v in victims
        ],
    }


# --------------------------------------------------------------- orchestration
def resolve_entities(graph_id: str, actors: Any, threshold: Optional[float] = None,
                     runtime: Any = None) -> Dict[str, Any]:
    """List entity nodes, embed names, plan merges, execute them (best-effort).

    Returns an audit dict (also written to handoff/entity_merges.json by the
    caller) with the planned clusters and per-merge execution results. Any failure
    degrades to a no-op partial result rather than raising — the GRAPH stage must
    never be broken by resolution.
    """
    from ..config import Config
    if threshold is None:
        threshold = float(getattr(Config, "GRAPH_RESOLVE_SIM_THRESHOLD", 0.88))
    if runtime is None:
        from .graphiti_client.runtime import get_runtime
        runtime = get_runtime()

    nodes = runtime.all_entity_nodes(graph_id) or []
    if len(nodes) < 2:
        return {"nodes_scanned": len(nodes), "clusters": 0, "merged_nodes": 0,
                "merges": [], "threshold": threshold}

    try:
        embeddings = runtime.embed_texts([nd.get("name", "") for nd in nodes])
    except Exception as exc:  # noqa: BLE001 — embeddings optional; fall back to exact-norm
        logger.warning("resolve_entities: embedding failed, exact-norm only: %s", exc)
        embeddings = None

    plan = plan_merges(nodes, embeddings, canonical_norm_set(actors), threshold)

    merged = 0
    for cluster in plan:
        results = []
        for victim in cluster["victims"]:
            try:
                res = runtime.merge_nodes(graph_id, cluster["survivor_uuid"], victim["uuid"])
                if res.get("deleted"):
                    merged += 1
                results.append({"victim_uuid": victim["uuid"], **res})
            except Exception as exc:  # noqa: BLE001 — per-merge isolation
                logger.warning("resolve_entities: merge failed %s←%s: %s",
                               cluster["survivor_uuid"], victim["uuid"], exc)
                results.append({"victim_uuid": victim["uuid"], "error": str(exc)})
        cluster["execution"] = results

    logger.info("resolve_entities[%s]: scanned=%d clusters=%d merged=%d (thr=%.2f)",
                graph_id, len(nodes), len(plan), merged, threshold)
    return {"nodes_scanned": len(nodes), "clusters": len(plan),
            "merged_nodes": merged, "merges": plan, "threshold": threshold}
