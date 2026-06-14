"""Drop-in ``Zep``-compatible client backed by a local Graphiti knowledge graph.

The app constructs ``Zep(api_key=...)`` and calls a small, well-defined set of
``client.graph.*`` methods. This facade reproduces that surface exactly, delegating to the
process-global :class:`GraphitiRuntime`. Returned node/edge/episode objects expose the
same attribute names the Zep SDK used (``uuid_``/``uuid``, ``fact``, ``valid_at`` ...), so
existing call sites — which read fields via ``getattr(x,'uuid_',...) or getattr(x,'uuid')``
or directly as ``x.uuid_`` — work unchanged.

graph_id is used verbatim as the Graphiti group_id / FalkorDB tenant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from .runtime import get_runtime


def _iso(v: Any) -> Any:
    """EXECPLAN F-2-2: normalize graphiti ``datetime`` temporal fields to ISO-8601
    strings at the facade boundary, restoring the documented Zep contract that
    downstream consumers (EdgeInfo/NodeInfo Optional[str], to_dict()/json.dumps)
    assume. Non-datetime values pass through unchanged."""
    return v.isoformat() if isinstance(v, datetime) else v


# ----------------------------------------------------------------------
# Zep-shaped wrapper objects
# ----------------------------------------------------------------------
class _ZepNode:
    """Mirrors a Zep graph node object."""

    def __init__(self, n: Any):
        uuid = getattr(n, "uuid", None) or getattr(n, "uuid_", None) or ""
        self.uuid_ = uuid
        self.uuid = uuid
        self.name = getattr(n, "name", "") or ""
        self.labels = list(getattr(n, "labels", None) or [])
        self.summary = getattr(n, "summary", "") or ""
        self.attributes = dict(getattr(n, "attributes", None) or {})
        self.created_at = _iso(getattr(n, "created_at", None))  # EXECPLAN F-2-2


class _ZepEdge:
    """Mirrors a Zep graph edge (fact) object, including the bi-temporal fields."""

    def __init__(self, e: Any):
        uuid = getattr(e, "uuid", None) or getattr(e, "uuid_", None) or ""
        self.uuid_ = uuid
        self.uuid = uuid
        self.name = getattr(e, "name", "") or ""
        self.fact = getattr(e, "fact", "") or ""
        self.fact_type = getattr(e, "name", "") or ""
        self.source_node_uuid = getattr(e, "source_node_uuid", "") or ""
        self.target_node_uuid = getattr(e, "target_node_uuid", "") or ""
        self.attributes = dict(getattr(e, "attributes", None) or {})
        # EXECPLAN F-2-2: graphiti returns datetimes here; Zep returned ISO strings.
        # Normalize so json.dumps consumers (EdgeInfo.to_dict) don't crash.
        self.created_at = _iso(getattr(e, "created_at", None))
        self.valid_at = _iso(getattr(e, "valid_at", None))
        self.invalid_at = _iso(getattr(e, "invalid_at", None))
        self.expired_at = _iso(getattr(e, "expired_at", None))
        episodes = list(getattr(e, "episodes", None) or [])
        self.episodes = episodes
        self.episode_ids = episodes


class _ZepEpisode:
    """Mirrors a Zep episode object. Graphiti processes synchronously, so ``processed``
    is always True by the time the episode handle is returned."""

    def __init__(self, uuid: str):
        self.uuid_ = uuid
        self.uuid = uuid
        self.processed = True


class _SearchResult:
    def __init__(self, edges: List[_ZepEdge], nodes: List[_ZepNode]):
        self.edges = edges
        self.nodes = nodes


# ----------------------------------------------------------------------
# namespaces
# ----------------------------------------------------------------------
class _NodeNamespace:
    def __init__(self, runtime):
        self._rt = runtime

    def get(self, uuid_: Optional[str] = None, graph_id: Optional[str] = None, **_: Any):
        node = self._rt.get_node(uuid_, graph_id=graph_id)
        return _ZepNode(node) if node is not None else None

    def get_by_graph_id(
        self, graph_id: str, limit: int = 100, uuid_cursor: Optional[str] = None, **_: Any
    ) -> List[_ZepNode]:
        nodes = self._rt.list_nodes(graph_id, limit, uuid_cursor)
        return [_ZepNode(n) for n in (nodes or [])]

    def get_entity_edges(
        self, node_uuid: Optional[str] = None, graph_id: Optional[str] = None, **_: Any
    ) -> List[_ZepEdge]:
        edges = self._rt.get_node_edges(node_uuid, graph_id=graph_id)
        return [_ZepEdge(e) for e in (edges or [])]


class _EdgeNamespace:
    def __init__(self, runtime):
        self._rt = runtime

    def get_by_graph_id(
        self, graph_id: str, limit: int = 100, uuid_cursor: Optional[str] = None, **_: Any
    ) -> List[_ZepEdge]:
        edges = self._rt.list_edges(graph_id, limit, uuid_cursor)
        return [_ZepEdge(e) for e in (edges or [])]


class _EpisodeNamespace:
    def __init__(self, runtime):
        self._rt = runtime

    def get(self, uuid_: Optional[str] = None, **_: Any) -> _ZepEpisode:
        # Graphiti ingestion is synchronous, so any episode handed back is already processed.
        return _ZepEpisode(uuid_ or "")


class _GraphNamespace:
    def __init__(self, runtime):
        self._rt = runtime
        self.node = _NodeNamespace(runtime)
        self.edge = _EdgeNamespace(runtime)
        self.episode = _EpisodeNamespace(runtime)

    # --- graph lifecycle -------------------------------------------------
    def create(self, graph_id: str, name: Optional[str] = None, description: Optional[str] = None, **_: Any):
        self._rt.create_graph(graph_id)
        return _ZepEpisode(graph_id)  # truthy handle; callers use their own graph_id

    def set_ontology(self, graph_ids: Optional[List[str]] = None, entities=None, edges=None, **_: Any):
        for gid in graph_ids or []:
            self._rt.set_ontology(gid, entities, edges)

    def delete(self, graph_id: str, **_: Any):
        self._rt.delete_graph(graph_id)

    # --- ingestion -------------------------------------------------------
    def add_batch(self, graph_id: str, episodes: List[Any], **_: Any) -> List[_ZepEpisode]:
        eps = [
            {
                "name": f"chunk-{i}",
                "data": getattr(ep, "data", "") or "",
                "type": getattr(ep, "type", "text") or "text",
                "source_description": "mirofish-text",
                "reference_time": getattr(ep, "reference_time", None),  # T2.3 bi-temporal
            }
            for i, ep in enumerate(episodes or [])
        ]
        # T2.5: opt-in concurrent ingest (GRAPH_BUILD_CONCURRENCY>1). Default 1 → the
        # serial path below, byte-identical to add_episode-in-a-loop.
        try:
            from ...config import Config

            concurrency = int(getattr(Config, "GRAPH_BUILD_CONCURRENCY", 1) or 1)
        except Exception:
            concurrency = 1
        if concurrency > 1 and len(eps) > 1:
            uuids = self._rt.add_episodes_concurrent(graph_id, eps, concurrency)
            return [_ZepEpisode(u) for u in uuids]
        out: List[_ZepEpisode] = []
        for ep in eps:
            uuid = self._rt.add_episode(
                graph_id,
                name=ep["name"],
                body=ep["data"],
                source_type=ep["type"],
                source_description=ep["source_description"],
                reference_time=ep["reference_time"],
            )
            out.append(_ZepEpisode(uuid))
        return out

    def add(self, graph_id: str, type: str = "text", data: str = "", **_: Any) -> _ZepEpisode:
        uuid = self._rt.add_episode(
            graph_id,
            name="activity",
            body=data,
            source_type=type,
            source_description="mirofish-activity",
        )
        return _ZepEpisode(uuid)

    def add_triplet(
        self,
        graph_id: str,
        source_name: str,
        edge_type: str,
        target_name: str,
        fact: str = "",
        valid_at: Any = None,
        source_label: str = "Entity",
        target_label: str = "Entity",
        **_: Any,
    ) -> str:
        """Write a known typed (source, edge_type, target) relationship (EXECPLAN T2.1).

        Endpoints are resolved/deduped by name+embedding, so seeding researched
        relationships before text ingest lets the prose extraction enrich the same
        nodes rather than duplicate them. Returns the edge uuid.
        """
        return self._rt.add_triplet(
            graph_id, source_name, edge_type, target_name, fact,
            valid_at, source_label, target_label,
        )

    def build_communities(self, graph_id: str, **_: Any) -> List[dict]:
        """T2.4: 在该图上跑社区发现，返回 [{uuid,name,summary}]。"""
        return self._rt.build_communities(graph_id)

    # --- retrieval -------------------------------------------------------
    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: Optional[str] = None,
        **_: Any,
    ) -> _SearchResult:
        edges, nodes = self._rt.search(graph_id, query, limit, scope)
        return _SearchResult([_ZepEdge(e) for e in edges], [_ZepNode(n) for n in nodes])


class Zep:
    """Drop-in replacement for ``zep_cloud.client.Zep`` backed by local Graphiti.

    ``api_key`` is accepted for signature compatibility but ignored (no cloud, no key).
    """

    def __init__(self, api_key: Optional[str] = None, **_: Any):
        self.api_key = api_key
        self.graph = _GraphNamespace(get_runtime())
