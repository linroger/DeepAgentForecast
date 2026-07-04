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
import concurrent.futures
import contextlib
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("mirofish.graphiti_runtime")

_VALID_BACKENDS = ("auto", "falkordblite", "falkordb", "kuzu")

# R2-KG-1 / R2-KG-3: causal attributes (sign / strength / lag / polarity / valence)
# are folded into an edge's ``fact`` text by graph_builder.seed_actors (e.g.
# "…（sign=-，strength=high，lag=2w，polarity=-0.80）"). The traversal primitives
# project the fact and parse those attributes back out so a multi-hop path carries
# per-hop sign/strength/lag and a computed NET polarity + cumulative lag — the
# difference between "A reaches B" and "A reaches B with this transmission sign/lag".
import re as _re

_FACT_ATTR_RE = {
    "sign": _re.compile(r"\bsign=([^\s，,）)、]+)", _re.IGNORECASE),
    "strength": _re.compile(r"\bstrength=([^\s，,）)、]+)", _re.IGNORECASE),
    "lag": _re.compile(r"\blag=([^\s，,）)、]+)", _re.IGNORECASE),
    "polarity": _re.compile(r"\bpolarity=([^\s，,）)、]+)", _re.IGNORECASE),
    "valence": _re.compile(r"\bvalence=([^\s，,）)、]+)", _re.IGNORECASE),
}


def _parse_edge_attrs(fact: str | None) -> dict:
    """Extract folded causal attrs from an edge fact. Missing keys → None (degrade-safe)."""
    out: dict = {"sign": None, "strength": None, "lag": None, "polarity": None, "valence": None}
    if not fact:
        return out
    for key, rx in _FACT_ATTR_RE.items():
        m = rx.search(fact)
        if m:
            out[key] = m.group(1).strip()
    return out


def _edge_polarity_sign(attrs: dict) -> int | None:
    """Map an edge's parsed attrs to a polarity sign in {+1,-1,None} for net-polarity math.

    Prefers a numeric ``polarity``; falls back to the ``sign`` token (+/-/positive/
    negative or a signed number). ``None`` when genuinely unknown (so net polarity can
    flag incomplete chains rather than silently assuming positive)."""
    pol = attrs.get("polarity")
    if pol is not None:
        try:
            v = float(pol)
            if v > 0:
                return 1
            if v < 0:
                return -1
        except (TypeError, ValueError):
            pass
    sign = (attrs.get("sign") or "").strip().lower()
    if sign in ("+", "positive", "pos", "up", "+1"):
        return 1
    if sign in ("-", "negative", "neg", "down", "-1"):
        return -1
    try:
        v = float(sign)
        if v > 0:
            return 1
        if v < 0:
            return -1
    except (TypeError, ValueError):
        pass
    return None


def _lag_numeric(lag: str | None) -> float | None:
    """Best-effort numeric magnitude of a lag token (unit-agnostic leading number)."""
    if not lag:
        return None
    m = _re.search(r"-?\d+(?:\.\d+)?", str(lag))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


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


def _default_op_timeout() -> float | None:
    """每个 Graphiti 操作（一次 add_episode / search / list…）的默认挂钟上限（秒）。

    读 Config.GRAPHITI_OP_TIMEOUT_S（config 默认 900 = 15 分钟，GRAPH-9 调低；本函数的
    getattr 兜底 1800 仅在 Config 缺该属性时生效）；0/负 = 不设上限（旧行为，无限等待）。
    给 sync→async 桥一个兜底，避免某次 LLM/DB 调用卡死时永久阻塞调用线程。
    """
    try:
        from ...config import Config
        v = float(getattr(Config, "GRAPHITI_OP_TIMEOUT_S", 1800) or 0)
        return v if v > 0 else None
    except Exception:  # noqa: BLE001 — 读不到配置就用保守的 30 分钟兜底
        return 1800.0


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
        # EXECPLAN2 F-12-8 / F-2-5: per-graph_id write lock registry. The cached
        # Graphiti instance is shared by all callers, and add_episode does a
        # search→resolve→write dedup sequence across await points; two concurrent
        # writers on the SAME graph_id (e.g. a scenario fork's feedback updater
        # overlapping the base run, or add_episodes_concurrent's fan-out) can both
        # miss the dedup lookup and create duplicate entity nodes. We serialize
        # mutating access per graph_id (different graph_ids still proceed in
        # parallel). The dict is only mutated on the single bg-loop thread, so its
        # creation needs no extra lock.
        self._graph_locks: dict = {}   # graph_id -> asyncio.Lock
        self._resolved_backend: str | None = None
        atexit.register(self._shutdown)

    # ------------------------------------------------------------------
    # sync -> async bridge
    # ------------------------------------------------------------------
    def run(self, coro, timeout: float | None = None):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        # 默认给每个操作一个挂钟上限（Config.GRAPHITI_OP_TIMEOUT_S，config 默认 900s；0=旧的无限等待），
        # 避免某次 LLM/DB 调用卡死时永久阻塞调用它的 Flask 线程。超时抛 TimeoutError，调用方一般已
        # try/except 降级（检索→本地回退、构图→失败可见）。显式传 timeout 时以显式值为准。
        if timeout is None:
            timeout = _default_op_timeout()
        eff = timeout if (timeout and timeout > 0) else None
        try:
            return future.result(eff)
        except concurrent.futures.TimeoutError:
            # CRITICAL: future.result() timing out does NOT stop the coroutine — it keeps
            # running on the single bg loop, and if it holds the per-graph write lock
            # (add_episode / merge_nodes / build_communities) that lock is NEVER released,
            # so every subsequent op + read on that graph_id deadlocks and times out in a
            # cascade (observed: graph builds fine, then all post-build reads time out for
            # 15min×retries while the DB itself answers the same query in 0.01s). Cancel
            # the asyncio task threadsafely so its `async with lock` unwinds via __aexit__.
            future.cancel()
            logger.warning("graphiti op exceeded %.0fs wall-clock; cancelled to release the "
                           "per-graph lock (avoids deadlocking later reads)", eff or 0)
            raise TimeoutError(f"graphiti operation exceeded {eff:.0f}s") from None

    # EXECPLAN2 F-12-8 / F-2-5: lazily create/return the per-graph_id write lock.
    # Must be awaited from the bg loop (the only thread that touches _graph_locks).
    def _graph_lock(self, graph_id: str) -> asyncio.Lock:
        lock = self._graph_locks.get(graph_id)
        if lock is None:
            lock = asyncio.Lock()
            self._graph_locks[graph_id] = lock
        return lock

    @staticmethod
    def _serialize_reads() -> bool:
        # EXECPLAN2 F-12-8: opt-in flag. When enabled, reads take the per-graph
        # write lock too (serializing reads against in-flight writes on the same
        # graph to avoid dirty/mid-transaction reads). Default off preserves
        # current read latency/behavior (reads proceed without blocking on writes).
        return (os.environ.get("GRAPH_SERIALIZE_READS", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    @contextlib.asynccontextmanager
    async def _read_guard(self, graph_id: str):
        # EXECPLAN2 F-12-8: when GRAPH_SERIALIZE_READS is enabled, take the per-graph
        # write lock for the duration of a read so it cannot observe a write's
        # mid-transaction state; otherwise this is a no-op (default behavior).
        if self._serialize_reads():
            async with self._graph_lock(graph_id):
                yield
        else:
            yield

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
            # throttling. GRAPH-3: the previous effective default of 8 throttled
            # intra-episode per-node/edge fan-out to ~3-4 waves; graphiti's own default
            # is 20. Promote to a documented default of 16 (sized jointly with
            # GRAPH_BUILD_CONCURRENCY and the dedicated LLM I/O pool); override via env.
            try:
                max_coros = int(os.environ.get("GRAPHITI_MAX_COROUTINES", "16"))
            except ValueError:
                max_coros = 16
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
        # EXECPLAN2 F-12-8 / F-2-5: serialize writes per graph_id so the
        # search→resolve→write dedup sequence is never interleaved with another
        # writer on the same graph (which would create duplicate same-name nodes).
        # asyncio.Lock is non-reentrant, so the actual write lives in the
        # lock-free _add_episode_locked helper that the concurrent fan-out (which
        # already holds the lock) calls directly.
        async with self._graph_lock(graph_id):
            return await self._add_episode_locked(
                graph_id,
                name=name,
                body=body,
                source_type=source_type,
                source_description=source_description,
                reference_time=reference_time,
            )

    async def _add_episode_locked(
        self, graph_id, *, name, body, source_type, source_description, reference_time
    ) -> str:
        """Write one episode. Caller MUST hold ``self._graph_lock(graph_id)``."""
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
        source_summary: str = "",
        target_summary: str = "",
    ) -> str:
        """Write a KNOWN (subject, predicate, object) edge directly (EXECPLAN T2.1).

        Unlike ``add_episode`` (which re-extracts entities/edges from prose via the
        LLM), this writes a researched relationship as a typed ``EntityEdge``.
        ``graphiti_core.add_triplet`` resolves/dedups the endpoint nodes by
        name+embedding, so a triplet whose endpoints already exist (text-extracted
        or previously seeded) attaches to them rather than duplicating. ``valid_at``
        stamps the edge's bi-temporal validity (T2.3).

        ``source_summary``/``target_summary`` (optional) seed each endpoint node's
        ``summary`` — graphiti's LLM dedup resolver reads node.summary to disambiguate
        same-name entities (Anthropic KG cookbook: descriptions are the resolution
        signal). Default "" keeps behavior identical to the pre-summary path.
        """
        return self.run(
            self._add_triplet(
                graph_id, source_name, edge_name, target_name, fact,
                valid_at, source_label, target_label,
                source_summary, target_summary,
            )
        )

    async def _add_triplet(
        self, graph_id, source_name, edge_name, target_name, fact,
        valid_at, source_label, target_label,
        source_summary="", target_summary="",
    ) -> str:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode

        g = await self._ensure_graph(graph_id)
        now = datetime.now(timezone.utc)

        def _labels(x):
            return ["Entity"] + ([x] if x and x != "Entity" else [])

        src = EntityNode(
            name=source_name, group_id=graph_id, labels=_labels(source_label),
            summary=source_summary or "", attributes={},
        )
        tgt = EntityNode(
            name=target_name, group_id=graph_id, labels=_labels(target_label),
            summary=target_summary or "", attributes={},
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
        # EXECPLAN2 F-12-8: serialize per graph_id — add_triplet also resolves/dedups
        # endpoint nodes by name+embedding, so a concurrent writer on the same graph
        # could duplicate endpoints.
        async with self._graph_lock(graph_id):
            await g.add_triplet(src, edge, tgt)
        return edge.uuid

    def add_episodes_concurrent(self, graph_id: str, episodes: list, concurrency: int = 4) -> list:
        """T2.5: ingest many episodes concurrently under a semaphore on the bg loop.

        ``episodes`` is a list of dicts ``{name?, data, type?, source_description?,
        reference_time?}``. Returns uuids in input order. The serial path
        (concurrency<=1, the default) is byte-identical to ``add_episode`` in a loop.

        WARNING (EXECPLAN2 F-2-5): with ``concurrency>1`` this fans out
        ``graphiti_core.add_episode`` calls that run concurrently against the SAME
        graph. ``graphiti_core`` has NO DB-side name-uniqueness constraint, and its
        entity resolution reads existing nodes BEFORE the in-flight nodes are
        committed; two episodes that mention the same new entity can therefore both
        miss the dedup lookup and each create a duplicate same-name node. This is
        an extraction-parallel / dedup-best-effort fast path, NOT a write-safe one.
        Keep ``GRAPH_BUILD_CONCURRENCY=1`` unless duplicate same-name entities are
        acceptable for the workload. The whole fan-out is still held under the
        per-graph write lock (F-12-8) so it never overlaps an EXTERNAL writer/reader
        on the same graph_id.
        """
        return self.run(self._add_episodes_concurrent(graph_id, episodes, concurrency))

    async def _add_episodes_concurrent(self, graph_id, episodes, concurrency):
        await self._ensure_graph(graph_id)  # warm once before fan-out (avoid lock stampede)
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(i, ep):
            async with sem:
                # Caller (below) already holds the per-graph write lock for the whole
                # batch, so call the lock-free core to avoid deadlocking on the
                # non-reentrant asyncio.Lock. The intra-batch dedup race documented
                # above is the accepted tradeoff of this opt-in fast path.
                return await self._add_episode_locked(
                    graph_id,
                    name=ep.get("name") or f"chunk-{i}",
                    body=ep.get("data", "") or "",
                    source_type=ep.get("type", "text") or "text",
                    source_description=ep.get("source_description", "") or "mirofish",
                    reference_time=ep.get("reference_time"),
                )

        # EXECPLAN2 F-12-8: hold the per-graph write lock across the entire fan-out so
        # the batch never overlaps an external writer/reader on the same graph_id.
        # RESILIENCE: return_exceptions=True so a SINGLE bad episode (e.g. a weak model
        # echoing the JSON schema instead of an instance, a transient extraction error)
        # is skipped+logged rather than aborting the whole batch → the whole graph stage.
        # Successful uuids are returned in input order; failures are dropped (the caller
        # counts the gap and hard-fails only if EVERYTHING failed).
        async with self._graph_lock(graph_id):
            results = await asyncio.gather(
                *[one(i, ep) for i, ep in enumerate(episodes)], return_exceptions=True
            )
        out = []
        n_failed = 0
        for idx, r in enumerate(results):
            if isinstance(r, BaseException):
                n_failed += 1
                logger.warning("[%s] episode %d ingest failed (skipped): %s",
                               graph_id, idx, str(r)[:160])
                continue
            if r:
                out.append(r)
        if n_failed:
            logger.warning("[%s] add_episodes_concurrent: %d/%d episodes skipped due to errors",
                           graph_id, n_failed, len(episodes))
        return out

    def build_communities(self, graph_id: str) -> list:
        """T2.4: Leiden 社区发现 + LLM 摘要，返回 [{uuid,name,summary}]。

        ``graphiti_core.build_communities`` 会先清空已有社区，故重跑不会累积。
        """
        return self.run(self._build_communities(graph_id))

    async def _build_communities(self, graph_id):
        g = await self._ensure_graph(graph_id)
        # EXECPLAN2 F-12-8: community building clears+rewrites community nodes for the
        # graph; serialize it per graph_id so it never overlaps a writer/reader.
        async with self._graph_lock(graph_id):
            nodes, _edges = await g.build_communities(group_ids=[graph_id])
        return [
            {"uuid": n.uuid, "name": n.name, "summary": getattr(n, "summary", "") or ""}
            for n in nodes
        ]

    # EXECPLAN2 I-1-2: list detected communities (Leiden) + their member entity
    # names, so the report's faction_brief tool and persona generation can read the
    # graph's OWN faction structure (with LLM-written summaries) instead of only the
    # action-log heuristic. Best-effort: any failure returns [] (caller degrades).
    def list_communities(self, graph_id: str) -> list:
        return self.run(self._list_communities(graph_id))

    async def _list_communities(self, graph_id):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.nodes import CommunityNode

        async with self._read_guard(graph_id):
            try:
                comms = await CommunityNode.get_by_group_ids(g.driver, [graph_id], limit=200)
            except Exception as exc:
                logger.debug("list_communities: get_by_group_ids failed for %s: %s", graph_id, exc)
                return []
            members_by_uuid: dict = {}
            try:
                records, _, _ = await g.driver.execute_query(
                    "MATCH (c:Community)-[:HAS_MEMBER]->(e:Entity) "
                    "RETURN c.uuid AS cuuid, collect(e.name) AS members"
                )
                for rec in (records or []):
                    cu = rec.get("cuuid")
                    if cu:
                        members_by_uuid[cu] = [m for m in (rec.get("members") or []) if m]
            except Exception as exc:
                logger.debug("list_communities: membership query failed for %s: %s", graph_id, exc)
        return [
            {
                "uuid": c.uuid,
                "name": c.name,
                "summary": getattr(c, "summary", "") or "",
                "members": members_by_uuid.get(c.uuid, []),
            }
            for c in comms
        ]

    # ── NEXTSTEPS P3-6: 多跳路径 / N-hop 子图遍历（FalkorDB 变长 Cypher）──
    # 预测是沿**传导机制**推理：「追踪级联，哪个节点一动就翻盘」。此前 runtime 只有 1-hop
    # get_node_edges + 向量重排，无可达/路径原语。这两个方法补上（按边 name 可过滤为因果族）。
    @staticmethod
    def _asof_predicate(as_of: Any):
        """R2-KG-4: build a Cypher point-in-time predicate over a var-length rel ``r``.

        Returns ``(clause, iso_param)``. ``as_of=None`` → ``("", None)`` so the query is
        unchanged (default OFF). Accepts a datetime or ISO string; an unparseable value
        degrades to no predicate (never raises)."""
        if as_of is None:
            return "", None
        try:
            if isinstance(as_of, datetime):
                iso = as_of.isoformat()
            else:
                s = str(as_of).strip()
                if not s:
                    return "", None
                iso = s
        except Exception:  # noqa: BLE001
            return "", None
        clause = (
            "AND all(x IN r WHERE (x.valid_at IS NULL OR x.valid_at <= $as_of) "
            "AND (x.invalid_at IS NULL OR x.invalid_at > $as_of)) "
        )
        return clause, iso

    def causal_paths(self, graph_id: str, source: str, target: str,
                     edge_types: list | None = None, max_hops: int = 4, limit: int = 20,
                     as_of: Any = None) -> list:
        """source→target 的有向多跳路径（每跳 RELATES_TO，可选按边 name 过滤为因果族）。

        返回 [{nodes:[name...], edges:[name...], hops:int, edge_details:[{name,sign,
        strength,polarity,lag,fact}...], net_polarity:'+'/'-'/'?'/None, lag_total:float|None}]；
        ``edges`` 保留为名字列表（向后兼容旧渲染），新键 ``edge_details`` 携带 R2-KG-1/R2-KG-3
        的逐跳因果属性。``as_of``（R2-KG-4，默认 None=关闭）可加双时态点过滤。
        失败/无路径 → []（degrade-safe）。"""
        return self.run(self._causal_paths(graph_id, source, target, edge_types, max_hops, limit, as_of))

    async def _causal_paths(self, graph_id, source, target, edge_types, max_hops, limit, as_of=None):
        try:
            g = await self._ensure_graph(graph_id)
        except Exception:  # noqa: BLE001
            return []
        hops = max(1, min(int(max_hops or 4), 6))      # 变长上界须为字面量；夹取后内插，安全
        lim = max(1, min(int(limit or 20), 100))
        params: dict = {"src": str(source), "tgt": str(target)}
        type_filter = ""
        if edge_types:
            # FalkorDB types the variable-length binding `r` as an Edge (not a List) for
            # length-1 matches, so `all(x IN r ...)` raises "expected List or Null but was
            # Edge". Iterate `relationships(p)` (always a List of edges) instead — portable.
            type_filter = "AND all(x IN relationships(p) WHERE x.name IN $types) "
            params["types"] = [str(t) for t in edge_types]
        # R2-KG-4: optional point-in-time predicate (each hop valid at as_of). Inert
        # when as_of is None (default) → query byte-identical to before.
        asof_filter, asof_param = self._asof_predicate(as_of)
        if asof_param is not None:
            params["as_of"] = asof_param
        cypher = (
            f"MATCH p=(a:Entity)-[r:RELATES_TO*1..{hops}]->(b:Entity) "
            f"WHERE a.name = $src AND b.name = $tgt {type_filter}{asof_filter}"
            "RETURN [n IN nodes(p) | n.name] AS nodes, "
            "[e IN relationships(p) | e.name] AS edges, "
            "[e IN relationships(p) | e.fact] AS facts "
            f"LIMIT {lim}"
        )
        try:
            async with self._read_guard(graph_id):
                records, _, _ = await g.driver.execute_query(cypher, **params)
        except Exception as exc:  # noqa: BLE001
            logger.debug("causal_paths failed %s (%s→%s): %s", graph_id, source, target, exc)
            return []
        out = []
        for rec in (records or []):
            nodes = [n for n in (rec.get("nodes") or []) if n]
            edges = [e for e in (rec.get("edges") or []) if e]
            facts = list(rec.get("facts") or [])
            if len(nodes) < 2:
                continue
            # R2-KG-1/R2-KG-3: per-hop causal attrs + computed net polarity & cumulative lag.
            details: list = []
            net = 1
            net_known = False
            lag_total = 0.0
            lag_seen = False
            for i, ename in enumerate(edges):
                fact = facts[i] if i < len(facts) else None
                attrs = _parse_edge_attrs(fact)
                ps = _edge_polarity_sign(attrs)
                if ps is not None:
                    net *= ps
                    net_known = True
                ln = _lag_numeric(attrs.get("lag"))
                if ln is not None:
                    lag_total += ln
                    lag_seen = True
                details.append({
                    "name": ename,
                    "sign": attrs.get("sign"),
                    "strength": attrs.get("strength"),
                    "polarity": attrs.get("polarity"),
                    "lag": attrs.get("lag"),
                    "fact": fact,
                })
            net_polarity = ("+" if net > 0 else "-") if net_known else None
            out.append({
                "nodes": nodes,
                "edges": edges,
                "hops": len(edges),
                "edge_details": details,
                "net_polarity": net_polarity,
                "lag_total": (lag_total if lag_seen else None),
            })
        return out

    def n_hop_subgraph(self, graph_id: str, center: str, max_hops: int = 2,
                       edge_types: list | None = None, limit: int = 60,
                       as_of: Any = None) -> list:
        """以 center 为中心的 N 跳邻域边集（无向扩展，可选按边 name 过滤）。
        返回 [{source, edge, target, sign, strength, polarity, lag, fact}]（去重）；
        ``source/edge/target`` 与旧版一致（向后兼容），新键为 R2-KG-1/R2-KG-3 逐边因果属性。
        ``as_of``（R2-KG-4，默认 None=关闭）可加双时态点过滤。失败 → []（degrade-safe）。"""
        return self.run(self._n_hop_subgraph(graph_id, center, max_hops, edge_types, limit, as_of))

    async def _n_hop_subgraph(self, graph_id, center, max_hops, edge_types, limit, as_of=None):
        try:
            g = await self._ensure_graph(graph_id)
        except Exception:  # noqa: BLE001
            return []
        hops = max(1, min(int(max_hops or 2), 4))
        lim = max(1, min(int(limit or 60), 300))
        params: dict = {"center": str(center)}
        type_filter = ""
        if edge_types:
            # FalkorDB types the variable-length binding `r` as an Edge (not a List) for
            # length-1 matches, so `all(x IN r ...)` raises "expected List or Null but was
            # Edge". Iterate `relationships(p)` (always a List of edges) instead — portable.
            type_filter = "AND all(x IN relationships(p) WHERE x.name IN $types) "
            params["types"] = [str(t) for t in edge_types]
        asof_filter, asof_param = self._asof_predicate(as_of)
        if asof_param is not None:
            params["as_of"] = asof_param
        cypher = (
            f"MATCH p=(a:Entity)-[r:RELATES_TO*1..{hops}]-(b:Entity) "
            f"WHERE a.name = $center {type_filter}{asof_filter}"
            "UNWIND relationships(p) AS rel "
            "RETURN DISTINCT startNode(rel).name AS source, rel.name AS edge, "
            "endNode(rel).name AS target, rel.fact AS fact "
            f"LIMIT {lim}"
        )
        try:
            async with self._read_guard(graph_id):
                records, _, _ = await g.driver.execute_query(cypher, **params)
        except Exception as exc:  # noqa: BLE001
            logger.debug("n_hop_subgraph failed %s (%s): %s", graph_id, center, exc)
            return []
        out = []
        for rec in (records or []):
            if not (rec.get("source") and rec.get("target")):
                continue
            fact = rec.get("fact")
            attrs = _parse_edge_attrs(fact)
            out.append({
                "source": rec.get("source"),
                "edge": rec.get("edge"),
                "target": rec.get("target"),
                "sign": attrs.get("sign"),
                "strength": attrs.get("strength"),
                "polarity": attrs.get("polarity"),
                "lag": attrs.get("lag"),
                "fact": fact,
            })
        return out

    # EXECPLAN2 I-1-0: map a recipe selector ('rrf'|'mmr'|'node_distance'|
    # 'cross_encoder'|'combined') + scope ('edges'|'nodes') to a graphiti_core
    # SearchConfig recipe. Unknown selectors fall back to 'rrf', so a stray value
    # degrades to today's behavior rather than erroring. The default selector is
    # 'rrf', which preserves the legacy {EDGE,NODE}_HYBRID_SEARCH_RRF call path.
    @staticmethod
    def _resolve_recipe(recipe, scope):
        from graphiti_core.search.search_config_recipes import (
            COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
            COMBINED_HYBRID_SEARCH_MMR,
            COMBINED_HYBRID_SEARCH_RRF,
            EDGE_HYBRID_SEARCH_CROSS_ENCODER,
            EDGE_HYBRID_SEARCH_MMR,
            EDGE_HYBRID_SEARCH_NODE_DISTANCE,
            EDGE_HYBRID_SEARCH_RRF,
            NODE_HYBRID_SEARCH_CROSS_ENCODER,
            NODE_HYBRID_SEARCH_MMR,
            NODE_HYBRID_SEARCH_NODE_DISTANCE,
            NODE_HYBRID_SEARCH_RRF,
        )

        sel = (recipe or "rrf").strip().lower()
        is_nodes = scope == "nodes"
        # 'combined' searches edges + nodes + communities + episodes in one pass,
        # so it is not split by scope (scope only chooses which layer for the
        # single-layer recipes below).
        combined = {
            "rrf": COMBINED_HYBRID_SEARCH_RRF,
            "mmr": COMBINED_HYBRID_SEARCH_MMR,
            "cross_encoder": COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
        }
        if sel == "combined":
            return COMBINED_HYBRID_SEARCH_RRF
        if sel.startswith("combined_"):
            return combined.get(sel[len("combined_"):], COMBINED_HYBRID_SEARCH_RRF)

        node_recipes = {
            "rrf": NODE_HYBRID_SEARCH_RRF,
            "mmr": NODE_HYBRID_SEARCH_MMR,
            "node_distance": NODE_HYBRID_SEARCH_NODE_DISTANCE,
            "cross_encoder": NODE_HYBRID_SEARCH_CROSS_ENCODER,
        }
        edge_recipes = {
            "rrf": EDGE_HYBRID_SEARCH_RRF,
            "mmr": EDGE_HYBRID_SEARCH_MMR,
            "node_distance": EDGE_HYBRID_SEARCH_NODE_DISTANCE,
            "cross_encoder": EDGE_HYBRID_SEARCH_CROSS_ENCODER,
        }
        table = node_recipes if is_nodes else edge_recipes
        return table.get(sel, table["rrf"])

    # EXECPLAN2 I-1-0 / I-1-1: translate a small, JSON-friendly dict into a real
    # graphiti_core SearchFilters so the facade can push typed (node_labels,
    # edge_types) and bi-temporal (valid_at / invalid_at) constraints down into the
    # graph query. Supported keys (all optional):
    #   node_labels: list[str]
    #   edge_types:  list[str]
    #   valid_at_before / valid_at_after / valid_at_at: ISO str | datetime
    #   invalid_at_before / invalid_at_after: ISO str | datetime
    #   invalid_at_is_null: bool  -> edge has no invalidation (still true)
    #   as_of: ISO str | datetime -> convenience for point-in-time (I-1-1):
    #          valid_at <= as_of AND (invalid_at IS NULL OR invalid_at > as_of)
    # A malformed dict degrades to an empty SearchFilters() (today's behavior)
    # rather than raising, so a bad filter never breaks retrieval.
    @staticmethod
    def _to_search_filters(spec):
        from graphiti_core.search.search_filters import (
            ComparisonOperator,
            DateFilter,
            SearchFilters,
        )

        if not spec:
            return None
        if not isinstance(spec, dict):
            return None

        def _dt(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                # Accept a trailing 'Z' (graphiti's own as-of payloads use it).
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return datetime.fromisoformat(s)
            return None

        try:
            kwargs: dict = {}
            node_labels = spec.get("node_labels")
            if node_labels:
                kwargs["node_labels"] = [str(x) for x in node_labels]
            edge_types = spec.get("edge_types")
            if edge_types:
                kwargs["edge_types"] = [str(x) for x in edge_types]

            # valid_at: a list-of-AND-lists; each inner list is ANDed, outer ORed.
            valid_and: list = []
            as_of = _dt(spec.get("as_of"))
            if as_of is not None:
                valid_and.append(
                    DateFilter(date=as_of, comparison_operator=ComparisonOperator.less_than_equal)
                )
            for key, op in (
                ("valid_at_before", ComparisonOperator.less_than),
                ("valid_at_after", ComparisonOperator.greater_than),
                ("valid_at_at", ComparisonOperator.equals),
            ):
                d = _dt(spec.get(key))
                if d is not None:
                    valid_and.append(DateFilter(date=d, comparison_operator=op))
            if valid_and:
                kwargs["valid_at"] = [valid_and]

            # invalid_at: treat null invalid_at as "still valid". For as-of we want
            #   (invalid_at IS NULL) OR (invalid_at > as_of)
            # expressed as two OR branches (outer list).
            invalid_or: list = []
            if as_of is not None:
                invalid_or.append(
                    [DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)]
                )
                invalid_or.append(
                    [DateFilter(date=as_of, comparison_operator=ComparisonOperator.greater_than)]
                )
            else:
                if spec.get("invalid_at_is_null"):
                    invalid_or.append(
                        [DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)]
                    )
                ib = _dt(spec.get("invalid_at_before"))
                if ib is not None:
                    invalid_or.append(
                        [DateFilter(date=ib, comparison_operator=ComparisonOperator.less_than)]
                    )
                ia = _dt(spec.get("invalid_at_after"))
                if ia is not None:
                    invalid_or.append(
                        [DateFilter(date=ia, comparison_operator=ComparisonOperator.greater_than)]
                    )
            if invalid_or:
                kwargs["invalid_at"] = invalid_or

            if not kwargs:
                # B-fix: 非空 spec 却没解析出任何已知过滤键（如把 valid_at_before 误写成
                # valid_before）→ 此前静默返回 None=全时段无过滤，对预测系统是 as-of 正确性
                # 隐患（"按时点检索却拿到时间压平结果"）。显式告警，让该退化可见。
                logger.warning(
                    "search_filter %r 未解析出任何已知过滤键，退化为全时段检索（请检查键名拼写）",
                    spec,
                )
                return None
            return SearchFilters(**kwargs)
        except Exception as exc:  # EXECPLAN2 I-1-0: bad filter -> degrade, never raise
            logger.warning("Ignoring malformed search_filter %r: %s", spec, exc)
            return None

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int,
        scope: str,
        recipe: str | None = None,
        center_node_uuid: str | None = None,
        bfs_origin_node_uuids: list | None = None,
        search_filter: dict | None = None,
    ):
        # EXECPLAN2 I-1-0: new params are all optional/defaulted; the legacy 4-arg
        # call (graph_id, query, limit, scope) is byte-identical to before because
        # recipe defaults to the configured 'rrf' selector and the rest are None.
        return self.run(
            self._search(
                graph_id,
                query,
                limit,
                scope,
                recipe=recipe,
                center_node_uuid=center_node_uuid,
                bfs_origin_node_uuids=bfs_origin_node_uuids,
                search_filter=search_filter,
            )
        )

    @staticmethod
    def _default_recipe() -> str:
        # EXECPLAN2 I-1-0: default retrieval recipe selector. OFF-by-default invariant:
        # 'rrf' reproduces the historical EDGE/NODE_HYBRID_SEARCH_RRF behavior exactly.
        # Owned files are runtime.py + client.py only, so read the flag via Config when
        # available, falling back to the GRAPH_SEARCH_RECIPE env var, then 'rrf'.
        try:
            from ...config import Config

            val = getattr(Config, "GRAPH_SEARCH_RECIPE", None)
            if val:
                return str(val).strip().lower()
        except Exception:
            pass
        return (os.environ.get("GRAPH_SEARCH_RECIPE", "rrf") or "rrf").strip().lower()

    async def _search(
        self,
        graph_id,
        query,
        limit,
        scope,
        recipe=None,
        center_node_uuid=None,
        bfs_origin_node_uuids=None,
        search_filter=None,
    ):
        g = await self._ensure_graph(graph_id)

        # EXECPLAN2 I-1-0: resolve the recipe selector. node_distance reranking only
        # works with a center node; without one, silently degrade to rrf to avoid a
        # graphiti SearchRerankerError that would break the legacy/default path.
        selector = (recipe or self._default_recipe() or "rrf").strip().lower()
        if selector == "node_distance" and not center_node_uuid:
            selector = "rrf"
        # KG-1: cross-encoder recipes with a NO-OP reranker are strictly worse than RRF —
        # graphiti truncates candidates to the first `limit` in dict-insertion order BEFORE
        # reranking, so a no-op rank means: no RRF fusion AND no semantic rerank. Degrade to
        # the RRF twin unless a real reranker (GRAPHITI_RERANKER=bge) is active.
        if selector in ("cross_encoder", "combined_cross_encoder"):
            try:
                from .cross_encoder import NoOpCrossEncoder

                if isinstance(self._get_clients()[2], NoOpCrossEncoder):
                    degraded = "combined" if selector == "combined_cross_encoder" else "rrf"
                    if not getattr(self, "_noop_ce_degrade_logged", False):
                        self._noop_ce_degrade_logged = True
                        logger.info(
                            "cross_encoder recipe requested but the active reranker is a "
                            "no-op (GRAPHITI_RERANKER!=bge); degrading '%s' -> '%s' to keep "
                            "real RRF fusion", selector, degraded,
                        )
                    selector = degraded
            except Exception:  # pragma: no cover - defensive; never break search on the guard
                pass
        config = self._resolve_recipe(selector, scope).model_copy(deep=True)
        config.limit = limit

        # EXECPLAN2 I-1-0: build a real SearchFilters from the dict spec; a malformed
        # spec degrades to None (no filter) so retrieval still returns results.
        filters = self._to_search_filters(search_filter)

        kwargs: dict = {"config": config, "group_ids": [graph_id]}
        if center_node_uuid:
            kwargs["center_node_uuid"] = center_node_uuid
        if bfs_origin_node_uuids:
            kwargs["bfs_origin_node_uuids"] = list(bfs_origin_node_uuids)
        if filters is not None:
            kwargs["search_filter"] = filters

        # EXECPLAN2 F-12-8: optionally serialize read-vs-write on this graph_id.
        async with self._read_guard(graph_id):
            results = await g.search_(query, **kwargs)
        return list(results.edges), list(results.nodes)

    def list_nodes(self, graph_id: str, limit: int, uuid_cursor):
        return self.run(self._list_nodes(graph_id, limit, uuid_cursor))

    async def _list_nodes(self, graph_id, limit, uuid_cursor):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.nodes import EntityNode

        # EXECPLAN2 F-12-8: optionally serialize read-vs-write on this graph_id.
        async with self._read_guard(graph_id):
            return await EntityNode.get_by_group_ids(
                g.driver, [graph_id], limit=limit, uuid_cursor=uuid_cursor
            )

    def list_edges(self, graph_id: str, limit: int, uuid_cursor):
        return self.run(self._list_edges(graph_id, limit, uuid_cursor))

    async def _list_edges(self, graph_id, limit, uuid_cursor):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.edges import EntityEdge
        from graphiti_core.errors import GroupsEdgesNotFoundError

        # EXECPLAN2 F-12-8: optionally serialize read-vs-write on this graph_id.
        try:
            async with self._read_guard(graph_id):
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
                # EXECPLAN2 F-12-8: optionally serialize read-vs-write on this graph_id.
                async with self._read_guard(gid):
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
                # EXECPLAN2 F-12-8: optionally serialize read-vs-write on this graph_id.
                async with self._read_guard(gid):
                    edges = await EntityEdge.get_by_node_uuid(g.driver, node_uuid)
                if edges:
                    return edges
            except Exception:  # pragma: no cover - defensive
                continue
        return []

    # ------------------------------------------------------------------
    # EXECPLAN2 I-1-4: entity-resolution primitives (list all nodes, embed
    # names, merge a duplicate node into a survivor). Used by zep_entity_resolver.
    # ------------------------------------------------------------------
    def all_entity_nodes(self, graph_id: str) -> list:
        """Return ALL entity nodes for a graph (paginated), as lightweight dicts."""
        return self.run(self._all_entity_nodes(graph_id))

    async def _all_entity_nodes(self, graph_id, page: int = 500):
        g = await self._ensure_graph(graph_id)
        from graphiti_core.nodes import EntityNode

        out: list = []
        cursor = None
        async with self._read_guard(graph_id):
            while True:
                batch = await EntityNode.get_by_group_ids(
                    g.driver, [graph_id], limit=page, uuid_cursor=cursor
                )
                if not batch:
                    break
                for n in batch:
                    out.append({
                        "uuid": n.uuid,
                        "name": n.name,
                        "labels": list(getattr(n, "labels", []) or []),
                        "summary": getattr(n, "summary", "") or "",
                        # 2026-07-03 live-surfaced：此前不带 attributes，导致
                        # zep_entity_resolver._node_aliases() 永远读不到任何别名信号
                        # （同一真实主体的多个别名表面形——如「China」「CCP」「Beijing」
                        # 「MOFCOM」——因此从未被识别为待合并对象，各自独立成节点，
                        # 挤占模拟 agent 池席位）。带上 attributes 让 alias-aware 合并
                        # 真正生效；缺失时默认 {}，与现状完全一致（degrade-safe）。
                        "attributes": dict(getattr(n, "attributes", {}) or {}),
                    })
                if len(batch) < page:
                    break
                cursor = batch[-1].uuid
        return out

    def embed_texts(self, texts: list) -> list:
        """Embed a list of strings into unit vectors (local sentence-transformer)."""
        return self.run(self._embed_texts(texts))

    async def _embed_texts(self, texts):
        embedder, _, _ = self._get_clients()
        if not texts:
            return []
        return await embedder.create_batch(list(texts))

    def merge_nodes(self, graph_id: str, survivor_uuid: str, victim_uuid: str) -> dict:
        """Merge ``victim`` entity node into ``survivor`` (EXECPLAN2 I-1-4).

        Rewires the victim's RELATES_TO edges onto the survivor (via graphiti_core's
        own EntityEdge.save — so the backend dialect, incl. reified RelatesToNode_
        backends, is handled by the library, not hand-written Cypher), unions
        attributes/labels onto the survivor, then DETACH-DELETEs the victim. A fresh
        edge uuid is assigned on rewire so the re-save can never collide with the
        victim's original edge uuid. Per-edge and overall best-effort: a failed step
        is logged and skipped (never half-corrupts beyond the offending edge). The
        whole pass is serialized on the per-graph write lock. Episodic MENTIONS edges
        to the victim are not rewired (provenance only) and drop with the victim.
        """
        return self.run(self._merge_nodes(graph_id, survivor_uuid, victim_uuid))

    async def _merge_nodes(self, graph_id, survivor_uuid, victim_uuid):
        import uuid as _uuidlib

        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode

        if survivor_uuid == victim_uuid:
            return {"rewired": 0, "deleted": False, "skipped": "same uuid"}
        g = await self._ensure_graph(graph_id)
        async with self._graph_lock(graph_id):
            try:
                survivor = await EntityNode.get_by_uuid(g.driver, survivor_uuid)
                victim = await EntityNode.get_by_uuid(g.driver, victim_uuid)
            except Exception as exc:
                logger.warning("merge_nodes: could not load nodes (%s): %s", graph_id, exc)
                return {"rewired": 0, "deleted": False, "skipped": "node load failed"}
            if survivor is None or victim is None:
                return {"rewired": 0, "deleted": False, "skipped": "node missing"}

            try:
                edges = await EntityEdge.get_by_node_uuid(g.driver, victim_uuid)
            except Exception as exc:
                logger.warning("merge_nodes: could not list victim edges (%s): %s", graph_id, exc)
                edges = []

            rewired = 0
            for e in edges:
                try:
                    touched = False
                    if e.source_node_uuid == victim_uuid:
                        e.source_node_uuid = survivor_uuid
                        touched = True
                    if e.target_node_uuid == victim_uuid:
                        e.target_node_uuid = survivor_uuid
                        touched = True
                    if not touched:
                        continue
                    if e.source_node_uuid == e.target_node_uuid:
                        continue  # self-loop after merge — drop (removed with victim)
                    e.uuid = str(_uuidlib.uuid4())  # fresh uuid: re-save can't collide
                    await e.save(g.driver)
                    rewired += 1
                except Exception as exc:
                    logger.warning("merge_nodes: edge rewire failed (%s): %s", graph_id, exc)

            # union victim attributes/labels onto survivor (never overwrite survivor's)
            try:
                v_attrs = dict(getattr(victim, "attributes", None) or {})
                s_attrs = dict(getattr(survivor, "attributes", None) or {})
                for k, val in v_attrs.items():
                    s_attrs.setdefault(k, val)
                if hasattr(survivor, "attributes"):
                    survivor.attributes = s_attrs
                survivor.labels = list(dict.fromkeys(
                    list(survivor.labels or []) + list(victim.labels or [])
                ))
                await survivor.save(g.driver)
            except Exception as exc:
                logger.warning("merge_nodes: survivor union failed (%s): %s", graph_id, exc)

            deleted = False
            try:
                await victim.delete(g.driver)
                deleted = True
            except Exception as exc:
                logger.warning("merge_nodes: victim delete failed (%s): %s", graph_id, exc)
        return {"rewired": rewired, "deleted": deleted}

    def delete_graph(self, graph_id: str) -> None:
        self.run(self._delete_graph(graph_id))

    async def _delete_graph(self, graph_id):
        # EXECPLAN2 F-12-8: serialize the delete against any in-flight writer/reader
        # on this graph_id so we never tear data out from under a live operation.
        async with self._graph_lock(graph_id):
            g = self._graphs.pop(graph_id, None)
            self._ontologies.pop(graph_id, None)

            # EXECPLAN2 F-2-1: drop the server-side FalkorDB graph data BEFORE closing
            # the driver. The previous code gated this on ``self._falkor_client``, which
            # is populated ONLY on the embedded (falkordblite) path — so on the
            # ``falkordb`` server backend (each driver builds its own client) the data
            # deletion was skipped entirely and the graph leaked on the server forever,
            # while the surrounding ``except: pass`` falsely reported success.
            #
            # Resolve the FalkorDB connection from the LIVE driver, which works for both
            # backends (driver.client is the AsyncFalkorDB/FalkorDB for embedded AND
            # server). Fall back to the shared embedded client when the graph was never
            # cached in this process.
            falkor_client = None
            if g is not None:
                falkor_client = getattr(getattr(g, "driver", None), "client", None)
            if falkor_client is None:
                falkor_client = self._falkor_client

            if falkor_client is not None and hasattr(falkor_client, "select_graph"):
                try:
                    graph = falkor_client.select_graph(graph_id)
                    await graph.delete()
                except Exception as exc:  # EXECPLAN2 F-2-1: surface the leak, don't swallow it
                    logger.warning("Failed to delete FalkorDB graph %s: %s", graph_id, exc)

            # EXECPLAN2 F-2-1: only close the driver when it does NOT wrap the shared
            # embedded client. On falkordblite every cached graph reuses the single
            # ``self._falkor_client``; closing one driver would aclose() that shared
            # connection and break every other graph. On the server backend each driver
            # owns its own client, so closing it frees the connection (no leak).
            if g is not None:
                driver_client = getattr(getattr(g, "driver", None), "client", None)
                shares_embedded_client = (
                    self._falkor_client is not None
                    and driver_client is self._falkor_client
                )
                if not shares_embedded_client:
                    try:
                        await g.close()
                    except Exception as exc:
                        logger.debug("Error closing graph %s after delete: %s", graph_id, exc)

        # NOTE (EXECPLAN2 F-12-8): intentionally retain the per-graph lock in the
        # registry. Popping it here could hand a freshly-created lock to a new caller
        # while another coroutine is still blocked on the old one for the same
        # graph_id, defeating the mutual exclusion. The registry is bounded by the
        # number of distinct graph_ids (same lifetime concern as self._graphs), so
        # the leak is negligible; correctness wins.

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
