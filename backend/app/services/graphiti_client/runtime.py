"""Process-global runtime that bridges the app's synchronous, threaded code to the
async, in-process Graphiti knowledge graph.

Responsibilities:
- Own a persistent asyncio event loop on a background thread (sync -> async bridge).
- Select & construct a LOCAL graph DB backend (no API key, no external service by default):
    * ``falkordblite`` (embedded FalkorDB via ``redislite.AsyncFalkorDB``) — preferred,
      Python >= 3.12, no Docker. This is the same engine Zep Cloud is built on.
    * a FalkorDB server (``FALKORDB_HOST``/``FALKORDB_PORT``) when one is provided/reachable.
    * ``kuzu`` (embedded, file-based) — optional fallback.
  Selected via ``GRAPH_BACKEND`` (default ``auto``).
- Provide the local LLM (provider-agnostic via the app's LLMClient), local embedder
  (sentence-transformers), and a local/no-op cross-encoder.
- Cache one ``Graphiti`` instance per graph_id and the per-graph custom ontology.

graph_id maps to BOTH the FalkorDB database/tenant name and the Graphiti ``group_id``
(identical string), so search/listing stay scoped per graph just like Zep's ``graph_id``.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger("mirofish.graphiti_runtime")

_VALID_BACKENDS = ("auto", "falkordblite", "falkordb", "kuzu")


def _data_dir() -> str:
    d = os.environ.get("GRAPHITI_DATA_DIR")
    if not d:
        try:
            from ...config import Config

            d = Config.GRAPHITI_DATA_DIR
        except Exception:
            # backend/app/services/graphiti_client -> backend/uploads/graphiti_db
            d = os.path.join(os.path.dirname(__file__), "..", "..", "..", "uploads", "graphiti_db")
    d = os.path.abspath(d)
    os.makedirs(d, exist_ok=True)
    return d


class GraphitiRuntime:
    """Singleton bridge to Graphiti. Access via ``get_runtime()``."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="graphiti-loop", daemon=True
        )
        self._thread.start()

        self._graphs: dict = {}        # graph_id -> Graphiti
        self._ontologies: dict = {}    # graph_id -> (entity_types, edge_types, edge_type_map)
        self._falkor_client = None
        self._embedder = None
        self._llm = None
        self._cross_encoder = None
        self._ensure_lock: asyncio.Lock | None = None
        self._resolved_backend: str | None = None
        atexit.register(self._shutdown)

    # ------------------------------------------------------------------
    # sync -> async bridge
    # ------------------------------------------------------------------
    def run(self, coro, timeout: float | None = None):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    # ------------------------------------------------------------------
    # backend resolution + driver construction
    # ------------------------------------------------------------------
    def _resolve_backend(self) -> str:
        if self._resolved_backend:
            return self._resolved_backend
        choice = (os.environ.get("GRAPH_BACKEND", "auto") or "auto").strip().lower()
        if choice not in _VALID_BACKENDS:
            choice = "auto"

        if choice == "auto":
            if os.environ.get("FALKORDB_HOST"):
                choice = "falkordb"
            elif self._can_import("redislite.async_falkordb_client"):
                choice = "falkordblite"
            elif self._can_import("kuzu"):
                choice = "kuzu"
            else:
                raise RuntimeError(
                    "No local graph backend available. Install one of: "
                    "'falkordblite' (embedded, recommended, Python>=3.12) or 'kuzu', "
                    "or run a FalkorDB server and set FALKORDB_HOST."
                )
        self._resolved_backend = choice
        logger.info("Graphiti graph backend: %s", choice)
        return choice

    @staticmethod
    def _can_import(module: str) -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    async def _get_falkor_client(self):
        if self._falkor_client is None:
            from redislite.async_falkordb_client import AsyncFalkorDB

            dbfile = os.path.join(_data_dir(), "falkor.db")
            self._falkor_client = AsyncFalkorDB(dbfilename=dbfile)
            logger.info("Started embedded FalkorDB (falkordblite) at %s", dbfile)
        return self._falkor_client

    async def _make_driver(self, graph_id: str):
        backend = self._resolve_backend()

        if backend == "falkordb":
            from .falkor_driver import SanitizingFalkorDriver

            host = os.environ.get("FALKORDB_HOST", "localhost")
            port = int(os.environ.get("FALKORDB_PORT", "6379"))
            password = os.environ.get("FALKORDB_PASSWORD") or None
            username = os.environ.get("FALKORDB_USERNAME") or None
            return SanitizingFalkorDriver(
                host=host, port=port, username=username, password=password, database=graph_id
            )

        if backend == "falkordblite":
            from .falkor_driver import SanitizingFalkorDriver

            client = await self._get_falkor_client()
            return SanitizingFalkorDriver(falkor_db=client, database=graph_id)

        if backend == "kuzu":
            from graphiti_core.driver.kuzu_driver import KuzuDriver

            path = os.path.join(_data_dir(), "kuzu", graph_id)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            driver = KuzuDriver(db=path)
            # Pin _database so add_episode does not attempt to clone the driver by group_id
            # (Kuzu has no per-database multi-tenancy; partitioning is by group_id property).
            driver._database = graph_id
            return driver

        raise RuntimeError(f"Unsupported graph backend: {backend}")

    def _get_clients(self):
        if self._embedder is None:
            from .embedder import LocalSentenceTransformerEmbedder
            from .llm_adapter import AppGraphitiLLMClient

            self._embedder = LocalSentenceTransformerEmbedder()
            self._llm = AppGraphitiLLMClient()

            reranker = (os.environ.get("GRAPHITI_RERANKER", "rrf") or "rrf").strip().lower()
            if reranker == "bge":
                from graphiti_core.cross_encoder.bge_reranker_client import BGERerankerClient

                self._cross_encoder = BGERerankerClient()
            else:
                from .cross_encoder import NoOpCrossEncoder

                self._cross_encoder = NoOpCrossEncoder()
        return self._embedder, self._llm, self._cross_encoder

    async def _ensure_graph(self, graph_id: str):
        if graph_id in self._graphs:
            return self._graphs[graph_id]
        if self._ensure_lock is None:
            self._ensure_lock = asyncio.Lock()
        async with self._ensure_lock:
            if graph_id in self._graphs:
                return self._graphs[graph_id]
            driver = await self._make_driver(graph_id)
            embedder, llm, cross_encoder = self._get_clients()
            from graphiti_core import Graphiti

            # Cap concurrent LLM extraction calls. Graph building drives the configured
            # LLM provider; unbounded concurrency to a hosted endpoint causes timeouts/
            # throttling. Default 8 (override via GRAPHITI_MAX_COROUTINES).
            try:
                max_coros = int(os.environ.get("GRAPHITI_MAX_COROUTINES", "8"))
            except ValueError:
                max_coros = 8
            g = Graphiti(
                graph_driver=driver,
                llm_client=llm,
                embedder=embedder,
                cross_encoder=cross_encoder,
                max_coroutines=max_coros,
            )
            # Keep group_id == driver._database so add_episode never clones the driver.
            try:
                g.driver._database = graph_id
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                await g.build_indices_and_constraints()
            except Exception as exc:  # indices may already exist; non-fatal
                logger.debug("build_indices_and_constraints note for %s: %s", graph_id, exc)
            self._graphs[graph_id] = g
            return g

    async def _candidate_graph_ids(self, graph_id: str | None) -> list[str]:
        if graph_id:
            return [graph_id]
        ids = list(self._graphs.keys())
        if ids:
            return ids
        # Cold cache (e.g. fresh process): try to discover existing FalkorDB graphs.
        try:
            if self._resolve_backend() in ("falkordblite", "falkordb"):
                client = await self._get_falkor_client() if self._resolve_backend() == "falkordblite" else None
                if client is not None and hasattr(client, "list_graphs"):
                    names = await client.list_graphs()
                    return [n for n in names if isinstance(n, str)]
        except Exception:  # pragma: no cover - discovery is best-effort
            pass
        return []

    # ------------------------------------------------------------------
    # public sync API (used by the Zep facade)
    # ------------------------------------------------------------------
    def create_graph(self, graph_id: str) -> None:
        """Pre-warm a graph (build indices) so it 'exists' immediately after create()."""
        self.run(self._ensure_graph(graph_id))

    def set_ontology(self, graph_id: str, entities, edges) -> None:
        entity_types = entities or None
        edge_types = None
        edge_type_map = None
        if edges:
            edge_types = {}
            edge_type_map = {}
            for name, value in edges.items():
                # value is (EdgeModelSubclass, [EntityEdgeSourceTarget, ...])
                if isinstance(value, (tuple, list)) and len(value) == 2:
                    model, source_targets = value
                else:
                    model, source_targets = value, []
                edge_types[name] = model
                for st in source_targets or []:
                    key = (getattr(st, "source", None) or "Entity", getattr(st, "target", None) or "Entity")
                    edge_type_map.setdefault(key, []).append(name)
            if not edge_type_map:
                edge_type_map = {("Entity", "Entity"): list(edge_types.keys())}
        self._ontologies[graph_id] = (entity_types, edge_types, edge_type_map)

    def add_episode(
        self,
        graph_id: str,
        *,
        name: str,
        body: str,
        source_type: str = "text",
        source_description: str = "",
        reference_time: datetime | None = None,
    ) -> str:
        return self.run(
            self._add_episode(
                graph_id,
                name=name,
                body=body,
                source_type=source_type,
                source_description=source_description,
                reference_time=reference_time,
            )
        )

    async def _add_episode(
        self, graph_id, *, name, body, source_type, source_description, reference_time
    ) -> str:
        g = await self._ensure_graph(graph_id)
        entity_types, edge_types, edge_type_map = self._ontologies.get(
            graph_id, (None, None, None)
        )
        from graphiti_core.nodes import EpisodeType

        source = {
            "text": EpisodeType.text,
            "json": EpisodeType.json,
            "message": EpisodeType.message,
        }.get((source_type or "text").lower(), EpisodeType.text)

        if reference_time is None:
            reference_time = datetime.now(timezone.utc)

        result = await g.add_episode(
            name=name,
            episode_body=body,
            source_description=source_description or "mirofish",
            reference_time=reference_time,
            source=source,
            group_id=graph_id,
            entity_types=entity_types,
            edge_types=edge_types,
            edge_type_map=edge_type_map,
        )
        return result.episode.uuid

    def add_triplet(
        self,
        graph_id: str,
        source_name: str,
        edge_name: str,
        target_name: str,
        fact: str,
        valid_at: datetime | None = None,
        source_label: str = "Entity",
        target_label: str = "Entity",
    ) -> str:
        """Write a KNOWN (subject, predicate, object) edge directly (EXECPLAN T2.1).

        Unlike ``add_episode`` (which re-extracts entities/edges from prose via the
        LLM), this writes a researched relationship as a typed ``EntityEdge``.
        ``graphiti_core.add_triplet`` resolves/dedups the endpoint nodes by
        name+embedding, so a triplet whose endpoints already exist (text-extracted
        or previously seeded) attaches to them rather than duplicating. ``valid_at``
        stamps the edge's bi-temporal validity (T2.3).
        """
        return self.run(
            self._add_triplet(
                graph_id, source_name, edge_name, target_name, fact,
                valid_at, source_label, target_label,
            )
        )

    async def _add_triplet(
        self, graph_id, source_name, edge_name, target_name, fact,
        valid_at, source_label, target_label,
    ) -> str:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode

        g = await self._ensure_graph(graph_id)
        now = datetime.now(timezone.utc)

        def _labels(x):
            return ["Entity"] + ([x] if x and x != "Entity" else [])

        src = EntityNode(
            name=source_name, group_id=graph_id, labels=_labels(source_label),
            summary="", attributes={},
        )
        tgt = EntityNode(
            name=target_name, group_id=graph_id, labels=_labels(target_label),
            summary="", attributes={},
        )
        edge = EntityEdge(
            name=edge_name,
            fact=fact or f"{source_name} {edge_name} {target_name}",
            group_id=graph_id,
            source_node_uuid=src.uuid,
            target_node_uuid=tgt.uuid,
            created_at=now,
            valid_at=valid_at,
            episodes=[],
            attributes={},
        )
        await g.add_triplet(src, edge, tgt)
        return edge.uuid

    def add_episodes_concurrent(self, graph_id: str, episodes: list, concurrency: int = 4) -> list:
        """T2.5: ingest many episodes concurrently under a semaphore on the bg loop.

        ``episodes`` is a list of dicts ``{name?, data, type?, source_description?,
        reference_time?}``. Returns uuids in input order. Trades a small dedup-ordering
        risk for a large speedup on big reports; the serial path (concurrency<=1) is
        byte-identical to ``add_episode`` in a loop.
        """
        return self.run(self._add_episodes_concurrent(graph_id, episodes, concurrency))

    async def _add_episodes_concurrent(self, graph_id, episodes, concurrency):
        await self._ensure_graph(graph_id)  # warm once before fan-out (avoid lock stampede)
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(i, ep):
            async with sem:
                return await self._add_episode(
                    graph_id,
                    name=ep.get("name") or f"chunk-{i}",
                    body=ep.get("data", "") or "",
                    source_type=ep.get("type", "text") or "text",
                    source_description=ep.get("source_description", "") or "mirofish",
                    reference_time=ep.get("reference_time"),
                )

        return list(await asyncio.gather(*[one(i, ep) for i, ep in enumerate(episodes)]))

    def build_communities(self, graph_id: str) -> list:
        """T2.4: Leiden 社区发现 + LLM 摘要，返回 [{uuid,name,summary}]。

        ``graphiti_core.build_communities`` 会先清空已有社区，故重跑不会累积。
        """
        return self.run(self._build_communities(graph_id))

    async def _build_communities(self, graph_id):
        g = await self._ensure_graph(graph_id)
        nodes, _edges = await g.build_communities(group_ids=[graph_id])
        return [
            {"uuid": n.uuid, "name": n.name, "summary": getattr(n, "summary", "") or ""}
            for n in nodes
        ]

    def search(self, graph_id: str, query: str, limit: int, scope: str):
        return self.run(self._search(graph_id, query, limit, scope))

    async def _search(self, graph_id, query, limit, scope):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.search.search_config_recipes import (
            EDGE_HYBRID_SEARCH_RRF,
            NODE_HYBRID_SEARCH_RRF,
        )

        recipe = NODE_HYBRID_SEARCH_RRF if scope == "nodes" else EDGE_HYBRID_SEARCH_RRF
        config = recipe.model_copy(deep=True)
        config.limit = limit
        results = await g.search_(query, config=config, group_ids=[graph_id])
        return list(results.edges), list(results.nodes)

    def list_nodes(self, graph_id: str, limit: int, uuid_cursor):
        return self.run(self._list_nodes(graph_id, limit, uuid_cursor))

    async def _list_nodes(self, graph_id, limit, uuid_cursor):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.nodes import EntityNode

        return await EntityNode.get_by_group_ids(
            g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
        )

    def list_edges(self, graph_id: str, limit: int, uuid_cursor):
        return self.run(self._list_edges(graph_id, limit, uuid_cursor))

    async def _list_edges(self, graph_id, limit, uuid_cursor):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.edges import EntityEdge
        from graphiti_core.errors import GroupsEdgesNotFoundError

        try:
            return await EntityEdge.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )
        except GroupsEdgesNotFoundError:
            return []

    def get_node(self, uuid: str, graph_id: str | None = None):
        return self.run(self._get_node(uuid, graph_id))

    async def _get_node(self, uuid, graph_id=None):
        from graphiti_core.errors import NodeNotFoundError
        from graphiti_core.nodes import EntityNode

        for gid in await self._candidate_graph_ids(graph_id):
            try:
                g = await self._ensure_graph(gid)
                return await EntityNode.get_by_uuid(g.driver, uuid)
            except NodeNotFoundError:
                continue
            except Exception:  # pragma: no cover - defensive
                continue
        return None

    def get_node_edges(self, node_uuid: str, graph_id: str | None = None):
        return self.run(self._get_node_edges(node_uuid, graph_id))

    async def _get_node_edges(self, node_uuid, graph_id=None):
        from graphiti_core.edges import EntityEdge

        for gid in await self._candidate_graph_ids(graph_id):
            try:
                g = await self._ensure_graph(gid)
                edges = await EntityEdge.get_by_node_uuid(g.driver, node_uuid)
                if edges:
                    return edges
            except Exception:  # pragma: no cover - defensive
                continue
        return []

    def delete_graph(self, graph_id: str) -> None:
        self.run(self._delete_graph(graph_id))

    async def _delete_graph(self, graph_id):
        g = self._graphs.pop(graph_id, None)
        self._ontologies.pop(graph_id, None)
        if g is not None:
            try:
                await g.close()
            except Exception:
                pass
        try:
            if self._falkor_client is not None and hasattr(self._falkor_client, "select_graph"):
                graph = self._falkor_client.select_graph(graph_id)
                await graph.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _shutdown(self):  # pragma: no cover - process teardown
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


_runtime: GraphitiRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> GraphitiRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = GraphitiRuntime()
    return _runtime
