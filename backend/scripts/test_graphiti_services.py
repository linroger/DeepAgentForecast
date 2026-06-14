"""Service-level integration test for the local-Graphiti migration.

Exercises the REAL service classes the pipeline uses (not the shim directly), so it
validates the frontend JSON contract and the report/OASIS retrieval paths end-to-end
against a local embedded FalkorDB — with no Zep key.

  GraphBuilderService:  create_graph -> set_ontology -> add_text_batches ->
                        _wait_for_episodes -> get_graph_data (frontend shape)
  ZepEntityReader:      filter_defined_entities (graph -> OASIS personas)
  ZepToolsService:      quick_search / insight_forge / panorama_search (report agent)
  ZepGraphMemoryUpdater: add_activity + flush (live simulation memory write-back)

Run: backend/.venv/bin/python backend/scripts/test_graphiti_services.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.graph_builder import GraphBuilderService  # noqa: E402
from app.services.zep_entity_reader import ZepEntityReader  # noqa: E402
from app.services.zep_tools import ZepToolsService  # noqa: E402
from app.services.zep_graph_memory_updater import ZepGraphMemoryUpdater, AgentActivity  # noqa: E402

ONTOLOGY = {
    "entity_types": [
        {"name": "Person", "description": "A leader, official, or public figure.",
         "attributes": [{"name": "role", "description": "their role or title"}]},
        {"name": "Organization", "description": "A company, country, or institution.",
         "attributes": [{"name": "kind", "description": "kind of organization"}]},
    ],
    "edge_types": [
        {"name": "INVOLVES", "description": "involvement or action between entities",
         "attributes": [{"name": "detail", "description": "detail"}],
         "source_targets": [{"source": "Organization", "target": "Organization"}]},
    ],
}

TEXT = (
    "TSMC, the Taiwanese chipmaker founded by Morris Chang, announced a new advanced "
    "semiconductor fabrication plant in Arizona, supported by the United States government "
    "through the CHIPS Act. Meanwhile the United States imposed advanced-semiconductor export "
    "controls on China. 中芯国际（SMIC）是中国领先的芯片制造商，受到这些出口管制的影响。"
    "荷兰公司ASML生产先进光刻机。NVIDIA designs AI chips and depends on TSMC for manufacturing; "
    "the export controls restrict NVIDIA from selling its most advanced GPUs to China."
)


def main():
    builder = GraphBuilderService()  # no Zep key needed
    graph_id = builder.create_graph("services-integration-test")
    print(f"[1] GraphBuilderService.create_graph -> {graph_id}")

    builder.set_ontology(graph_id, ONTOLOGY)
    print("[2] set_ontology OK")

    from app.services.text_processor import TextProcessor
    chunks = TextProcessor.split_text(TEXT, 350, 40)
    print(f"[3] split into {len(chunks)} chunks")

    t0 = time.time()
    uuids = builder.add_text_batches(graph_id, chunks, batch_size=5)
    print(f"[4] add_text_batches -> {len(uuids)} episodes in {time.time()-t0:.1f}s")
    builder._wait_for_episodes(uuids)  # no-op for local Graphiti (already processed)
    print("[5] _wait_for_episodes OK (no-op locally)")

    # ---- frontend contract: get_graph_data ----
    data = builder.get_graph_data(graph_id)
    assert set(["graph_id", "nodes", "edges", "node_count", "edge_count"]).issubset(data), data.keys()
    print(f"[6] get_graph_data: {data['node_count']} nodes, {data['edge_count']} edges")
    assert data["node_count"] > 0 and data["edge_count"] > 0, "empty graph"
    n0 = data["nodes"][0]
    e0 = data["edges"][0]
    assert {"uuid", "name", "labels", "summary", "attributes", "created_at"}.issubset(n0), n0.keys()
    assert {"uuid", "name", "fact", "fact_type", "source_node_uuid", "target_node_uuid",
            "source_node_name", "target_node_name", "attributes", "created_at",
            "valid_at", "invalid_at", "expired_at", "episodes"}.issubset(e0), e0.keys()
    print(f"      node[0] keys OK: {n0['name']!r} labels={n0['labels']}")
    print(f"      edge[0] keys OK: {e0['name']} | {e0['fact'][:50]!r} src_name={e0['source_node_name']!r}")

    # ---- OASIS path: ZepEntityReader ----
    reader = ZepEntityReader()
    filtered = reader.filter_defined_entities(graph_id, enrich_with_edges=True)
    fd = filtered.to_dict()
    print(f"[7] ZepEntityReader.filter_defined_entities -> {fd['filtered_count']} typed entities, "
          f"types={sorted(fd['entity_types'])}")
    assert fd["filtered_count"] > 0, "no typed entities for OASIS"

    # ---- report agent path: ZepToolsService ----
    tools = ZepToolsService()
    qs = tools.quick_search(graph_id, "semiconductor export controls", limit=8)
    print(f"[8] ZepToolsService.quick_search -> {qs.total_count} facts; sample: {qs.facts[:2]}")
    assert qs.total_count > 0, "quick_search empty"

    pano = tools.panorama_search(graph_id, "TSMC China NVIDIA", limit=30)
    print(f"[9] panorama_search -> active={pano.active_count} historical={pano.historical_count} "
          f"nodes={pano.total_nodes} edges={pano.total_edges}")
    assert pano.total_nodes > 0 and pano.total_edges > 0

    forge = tools.insight_forge(graph_id, "How do US export controls affect China's chip industry?",
                                simulation_requirement="semiconductor geopolitics", max_sub_queries=3)
    print(f"[10] insight_forge -> facts={forge.total_facts} entities={forge.total_entities} "
          f"chains={forge.total_relationships}")
    assert forge.total_facts > 0

    # ---- live memory write-back: ZepGraphMemoryUpdater ----
    updater = ZepGraphMemoryUpdater(graph_id=graph_id)
    updater.start()
    updater.add_activity(AgentActivity(
        platform="twitter", agent_id=1, agent_name="Analyst_1", action_type="CREATE_POST",
        action_args={"content": "China will accelerate domestic chip self-sufficiency in response to controls."},
        round_num=1, timestamp="2026-06-13T00:00:00"))
    updater.add_activity(AgentActivity(
        platform="reddit", agent_id=2, agent_name="Analyst_2", action_type="CREATE_POST",
        action_args={"content": "ASML lithography restrictions are the key chokepoint."},
        round_num=1, timestamp="2026-06-13T00:00:01"))
    time.sleep(2.0)
    updater.stop()
    stats = updater.get_stats()
    print(f"[11] ZepGraphMemoryUpdater stats: {stats}")

    print("\nRESULT: PASS — full service stack runs on local Graphiti with no Zep key.")
    print(f"        (graph_id={graph_id})")


if __name__ == "__main__":
    main()
