"""End-to-end smoke test for the local-Graphiti migration (no Zep, no Docker).

Exercises the full shim path: create -> set_ontology -> add_batch (LLM extraction +
local embeddings into embedded FalkorDB) -> list nodes/edges -> hybrid search (edges+nodes).

Run:  backend/.venv/bin/python backend/scripts/test_graphiti_migration.py
Requires a configured LLM provider in .env (uses the app's LLMClient for extraction).
"""

import os
import sys
import time
import uuid
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import Field  # noqa: E402

from app.services.graphiti_client import Zep, EpisodeData, EntityEdgeSourceTarget  # noqa: E402
from app.services.graphiti_client.ontology import EntityModel, EntityText, EdgeModel  # noqa: E402
from app.utils.zep_paging import fetch_all_nodes, fetch_all_edges  # noqa: E402


def build_ontology():
    """Mirror graph_builder.set_ontology's dynamic-class construction."""
    person_attrs = {
        "__doc__": "A person such as a leader, official, or public figure.",
        "__annotations__": {"role": Optional[EntityText]},
        "role": Field(description="the person's role or title", default=None),
    }
    org_attrs = {
        "__doc__": "An organization, company, country, or institution.",
        "__annotations__": {"kind": Optional[EntityText]},
        "kind": Field(description="kind of organization", default=None),
    }
    Person = type("Person", (EntityModel,), person_attrs)
    Organization = type("Organization", (EntityModel,), org_attrs)

    rel_attrs = {
        "__doc__": "A relationship indicating involvement or action between entities.",
        "__annotations__": {"detail": Optional[str]},
        "detail": Field(description="detail of the relationship", default=None),
    }
    Involves = type("Involves", (EdgeModel,), rel_attrs)

    entities = {"Person": Person, "Organization": Organization}
    edges = {
        "INVOLVES": (
            Involves,
            [EntityEdgeSourceTarget(source="Organization", target="Organization")],
        )
    }
    return entities, edges


CHUNKS = [
    "TSMC, the Taiwanese chipmaker, announced a new advanced semiconductor fabrication "
    "plant in Arizona. The United States government supported the investment through the "
    "CHIPS Act. Morris Chang founded TSMC.",
    "美国对中国实施了先进半导体出口管制。中芯国际（SMIC）是中国领先的芯片制造商，"
    "受到这些管制的影响。荷兰公司ASML生产光刻机。",
    "NVIDIA designs AI chips and relies on TSMC for manufacturing. The export controls "
    "restrict NVIDIA from selling its most advanced GPUs to China.",
]


def main():
    graph_id = f"mirofish_test{uuid.uuid4().hex[:10]}"
    print(f"[1] graph_id = {graph_id}")
    client = Zep(api_key="ignored")

    client.graph.create(graph_id=graph_id, name="migration-test")
    print("[2] create_graph OK (indices built)")

    entities, edges = build_ontology()
    client.graph.set_ontology(graph_ids=[graph_id], entities=entities, edges=edges)
    print("[3] set_ontology OK")

    t0 = time.time()
    episodes = [EpisodeData(data=c, type="text") for c in CHUNKS]
    results = client.graph.add_batch(graph_id=graph_id, episodes=episodes)
    print(f"[4] add_batch OK: {len(results)} episodes in {time.time()-t0:.1f}s "
          f"(uuids: {[r.uuid_[:8] for r in results]})")

    nodes = fetch_all_nodes(client, graph_id)
    edges_list = fetch_all_edges(client, graph_id)
    print(f"[5] fetch_all: {len(nodes)} nodes, {len(edges_list)} edges")
    for n in nodes[:12]:
        types = [l for l in n.labels if l not in ("Entity", "Node")]
        print(f"      node: {n.name!r} labels={types} summary={n.summary[:50]!r}")
    for e in edges_list[:12]:
        print(f"      edge: {e.name} | {e.fact[:70]!r} valid_at={e.valid_at}")

    print("[6] search(scope=edges, query='export controls'):")
    sr = client.graph.search(graph_id=graph_id, query="semiconductor export controls", limit=8, scope="edges")
    for e in sr.edges[:6]:
        print(f"      fact: {e.fact[:80]!r}")

    print("[7] search(scope=nodes, query='chip manufacturer'):")
    snr = client.graph.search(graph_id=graph_id, query="chip manufacturer company", limit=6, scope="nodes")
    for n in snr.nodes[:6]:
        print(f"      node: {n.name!r} summary={n.summary[:60]!r}")

    # single-node lookup without graph_id (insight_forge / entity_reader pattern)
    if nodes:
        got = client.graph.node.get(uuid_=nodes[0].uuid_)
        print(f"[8] node.get(no graph_id) -> {got.name if got else None!r}")
        incident = client.graph.node.get_entity_edges(node_uuid=nodes[0].uuid_)
        print(f"[9] node.get_entity_edges -> {len(incident)} incident edges")

    assert len(nodes) > 0, "no nodes extracted"
    assert len(edges_list) > 0, "no edges extracted"
    print("\nRESULT: PASS — local Graphiti build + read + search works with no Zep key.")


if __name__ == "__main__":
    main()
