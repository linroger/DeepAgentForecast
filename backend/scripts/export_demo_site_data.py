#!/usr/bin/env python3
"""Export full pipeline artifacts for the live demo site (docs/demos/<run>/).

For each showcased run this dumps every stage of the workflow:
    research_log.txt   deep-research console log (stage 1)
    dossier.md         research brief (stage 1 output)
    actors.json        researched actor profiles (if extracted)
    sources.json       cited web sources (if extracted)
    ontology.json      entity/edge types + analysis summary (stage 2)
    graph.json         Zep knowledge-graph nodes + edges (stage 3)
    forum.json         simulated Twitter/Reddit feed from actions.jsonl (stage 5)
    report.md          final forecast report (stage 6), placeholder sections stripped
    meta.json          prompt, dates, rounds, persona count

Graph export hits the Zep API (read-only) and respects the same 429
retry-after handling as the app. Use --skip-graph to re-export everything
else without network calls.

Usage:
    cd backend && uv run python scripts/export_demo_site_data.py [--skip-graph] [--only RUN_KEY]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPLOADS = os.path.join(ROOT, "backend", "uploads")
OUT_ROOT = os.path.join(ROOT, "docs", "demos")

# run key (used in site URLs) -> pipeline id
RUNS = {
    "us-ai-2030": "pipe_2a5b07f9f8c1",
    "ev-2035": "pipe_66838c1c67de",
    "russia-ukraine": "pipe_8b47373016f1",
}

PLACEHOLDER_MARKER = "本章节生成失败"


def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def strip_placeholder_sections(md: str) -> str:
    """Drop H2 sections whose entire body is a generation-failure placeholder."""
    parts = re.split(r"(?m)^(## .+)$", md)
    # parts: [prefix, h2, body, h2, body, ...]
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        heading, body = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
        stripped = body.strip()
        if PLACEHOLDER_MARKER in stripped and len(stripped) < 300:
            continue
        out.append(heading + body)
    return "".join(out)


def export_forum(sim_dir: str) -> dict:
    """Parse twitter/reddit actions.jsonl into a compact feed for the site."""
    feed = {}
    for plat in ("twitter", "reddit"):
        rows = []
        path = os.path.join(sim_dir, plat, "actions.jsonl")
        if not os.path.exists(path):
            feed[plat] = rows
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("event_type"):  # round_start / simulation_start markers
                    continue
                args = e.get("action_args") or {}
                content = args.get("content") or args.get("text") or ""
                rows.append({
                    "round": e.get("round"),
                    "agent": e.get("agent_name") or f"Agent_{e.get('agent_id')}",
                    "type": (e.get("action_type") or "").upper(),
                    "content": content,
                    "ok": bool(e.get("success", True)),
                })
        feed[plat] = rows
    return feed


def rebuild_graph(key: str, out_dir: str) -> str:
    """Re-create the run's knowledge graph on Zep from its saved dossier + ontology.

    Used when the original graph no longer exists on the Zep account (account
    rotation / retention). This re-runs the pipeline's stage-3 on identical
    inputs — same dossier text, same chunking, same ontology — so the exported
    graph is a faithful reconstruction of what the run built.
    """
    from app.services.graph_builder import GraphBuilderService
    from app.services.text_processor import TextProcessor

    dossier = open(os.path.join(out_dir, "dossier.md"), encoding="utf-8").read()
    ontology = _read_json(os.path.join(out_dir, "ontology.json")) or {}

    builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
    graph_id = builder.create_graph(name=f"demo_{key}")
    builder.set_ontology(graph_id, {
        "entity_types": ontology.get("entity_types", []),
        "edge_types": ontology.get("edge_types", []),
    })
    chunks = TextProcessor.split_text(dossier, Config.DEFAULT_CHUNK_SIZE, Config.DEFAULT_CHUNK_OVERLAP)
    print(f"   rebuilding graph for {key}: {len(chunks)} chunks -> {graph_id}")
    uuids = builder.add_text_batches(graph_id, chunks, batch_size=10,
                                     progress_callback=lambda m, r: None)
    builder._wait_for_episodes(uuids, lambda m, r: print(f"   … {m}", flush=True) if r in (0.0, 1.0) else None)
    return graph_id


def export_graph(graph_id: str) -> dict:
    """Fetch nodes/edges from Zep and trim to what the site renderer needs."""
    from app.services.graph_builder import GraphBuilderService

    builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
    data = builder.get_graph_data(graph_id)
    nodes = [{
        "id": n["uuid"],
        "name": n.get("name") or "",
        "labels": [l for l in (n.get("labels") or []) if l != "Entity"],
        "summary": (n.get("summary") or "")[:600],
    } for n in data.get("nodes", [])]
    node_ids = {n["id"] for n in nodes}
    links = [{
        "source": e.get("source_node_uuid"),
        "target": e.get("target_node_uuid"),
        "name": e.get("name") or "",
        "fact": (e.get("fact") or "")[:400],
    } for e in data.get("edges", [])
        if e.get("source_node_uuid") in node_ids and e.get("target_node_uuid") in node_ids]
    return {"nodes": nodes, "links": links}


def export_run(key: str, pipeline_id: str, skip_graph: bool) -> None:
    state = _read_json(os.path.join(UPLOADS, "pipelines", pipeline_id, "pipeline_state.json"))
    if not state:
        print(f"!! {key}: pipeline state missing, skipped")
        return
    out = os.path.join(OUT_ROOT, key)
    os.makedirs(out, exist_ok=True)
    handoff = os.path.join(UPLOADS, "pipelines", pipeline_id, "handoff")

    # stage 1 — research log + dossier (+ structured extraction when present)
    log_src = os.path.join(handoff, "research_progress.log")
    if os.path.exists(log_src):
        shutil.copyfile(log_src, os.path.join(out, "research_log.txt"))
    shutil.copyfile(os.path.join(handoff, "research_report.md"), os.path.join(out, "dossier.md"))
    for name in ("actors.json", "sources.json"):
        src = os.path.join(handoff, name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out, name))

    # stage 2 — ontology
    project = _read_json(os.path.join(UPLOADS, "projects", state["project_id"], "project.json")) or {}
    ontology = project.get("ontology") or {}
    _write_json(os.path.join(out, "ontology.json"), {
        "entity_types": ontology.get("entity_types", []),
        "edge_types": ontology.get("edge_types", []),
        "analysis_summary": project.get("analysis_summary", ""),
    })

    # stage 3 — knowledge graph (network call; resilient to Zep 429s). If the
    # original graph no longer exists on the account, rebuild it from the
    # saved dossier + ontology (identical stage-3 inputs) and export that.
    if not skip_graph:
        from zep_cloud.core.api_error import ApiError

        graph_id = state["graph_id"]
        try:
            graph = export_graph(graph_id)
        except ApiError as e:
            if getattr(e, "status_code", None) != 404:
                raise
            print(f"   original graph {graph_id} is gone (404) — rebuilding from dossier")
            graph_id = rebuild_graph(key, out)
            graph = export_graph(graph_id)
        graph["graph_id"] = graph_id
        _write_json(os.path.join(out, "graph.json"), graph)
        print(f"   graph: {len(graph['nodes'])} nodes / {len(graph['links'])} edges")

    # stage 5 — forum feed
    forum = export_forum(os.path.join(UPLOADS, "simulations", state["simulation_id"]))
    _write_json(os.path.join(out, "forum.json"), forum)

    # stage 6 — final report (placeholder sections stripped for presentation)
    report_md = open(os.path.join(UPLOADS, "reports", state["report_id"], "full_report.md"),
                     encoding="utf-8").read()
    cleaned = strip_placeholder_sections(report_md)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(cleaned)

    # run metadata for the site cards/header
    run_state = _read_json(os.path.join(UPLOADS, "simulations", state["simulation_id"], "run_state.json")) or {}
    cfg = _read_json(os.path.join(UPLOADS, "simulations", state["simulation_id"], "simulation_config.json")) or {}
    agents = cfg.get("agent_configs") or cfg.get("agents") or []
    _write_json(os.path.join(out, "meta.json"), {
        "pipeline_id": pipeline_id,
        "prompt": state.get("prompt", ""),
        "created_at": state.get("created_at", ""),
        "mode": state.get("mode", "full"),
        "rounds": run_state.get("total_rounds"),
        "personas": len(agents) if isinstance(agents, list) else None,
        "has_actors": os.path.exists(os.path.join(handoff, "actors.json")),
    })
    print(f"ok {key}: exported -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-graph", action="store_true", help="skip the Zep graph export (no network)")
    ap.add_argument("--only", help="export a single run key")
    args = ap.parse_args()

    runs = {args.only: RUNS[args.only]} if args.only else RUNS
    for key, pid in runs.items():
        export_run(key, pid, skip_graph=args.skip_graph)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
