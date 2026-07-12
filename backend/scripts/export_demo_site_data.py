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
import hashlib
import json
import os
import re
import shutil
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Config  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPLOADS = os.path.join(ROOT, "backend", "uploads")
OUT_ROOT = os.path.join(ROOT, "docs", "demos")

# run key (used in site URLs) -> pipeline id
RUNS = {
    "us-ai-2030": "pipe_2a5b07f9f8c1",
    "ev-2035": "pipe_91aaf91f6392",
    "russia-ukraine": "pipe_8b47373016f1",
    "semiconductors-2030": "pipe_f01ed9fe06de",
    "memory-semi-2030": "pipe_e2egold02",
    "china-storage-2035": "pipe_764249df9c38",
    "us-iran-2026": "pipe_a90b338fdfa0",
    # MiniMax (minimax-m3) demo runs — added 2026-06-21
    "storage-semi-2028": "pipe_41522d5d9790",
    "cloud-2030": "pipe_85d91bafe6fd",
    "collision-decade-2031": "pipe_a335177097fb",
    # added 2026-07-04 — GRAPH-12 schema-echo-unwrap validation runs
    "us-trade-2028": "pipe_bf2bb3095d11",
    "us-midterms-2026": "pipe_aa0fb94abe92",
}

PLACEHOLDER_MARKER = "本章节生成失败"
REQUIRED_STAGES = ("research", "ontology", "graph", "prepare", "run", "report")
MARKDOWN_LINK_RE = re.compile(
    r"(?P<prefix>!?\[[^\]\r\n]*\]\()(?P<target>[^)\r\n]*)(?P<suffix>\))"
)
MARKDOWN_TARGET_RE = re.compile(
    r'''^(?P<url><[^>\r\n]+>|[^\s\r\n]+)(?P<title>\s+(?:"[^"\r\n]*"|'[^'\r\n]*'))?$'''
)


def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_publishable_run(pipeline_id: str, state: dict, uploads: str = UPLOADS) -> dict:
    """Fail closed unless a pipeline's immutable final artifacts are publishable."""
    issues = []
    if state.get("pipeline_id") not in (None, pipeline_id):
        issues.append("pipeline id does not match its state")
    if state.get("status") != "completed":
        issues.append(f"pipeline status is {state.get('status')!r}, not 'completed'")

    stages = state.get("stages") or {}
    for name in REQUIRED_STAGES:
        stage = stages.get(name) or {}
        if stage.get("status") != "completed" or stage.get("error"):
            issues.append(f"stage {name!r} is not cleanly completed")

    report_id = state.get("report_id")
    simulation_id = state.get("simulation_id")
    graph_id = state.get("graph_id")
    if not report_id:
        issues.append("report id is missing")
    if not simulation_id:
        issues.append("simulation id is missing")
    if not graph_id:
        issues.append("graph id is missing")

    report_dir = os.path.join(uploads, "reports", str(report_id or ""))
    report_path = os.path.join(report_dir, "full_report.md")
    forecast_path = os.path.join(report_dir, "forecast.json")
    audit_path = os.path.join(report_dir, "final_audit.json")
    audit = _read_json(audit_path)
    if not isinstance(audit, dict):
        issues.append("final read-only audit is missing or invalid")
        audit = {}
    else:
        if audit.get("report_id") not in (None, report_id):
            issues.append("final audit report id does not match")
        if audit.get("read_only") is not True:
            issues.append("final audit is not read-only")
        if audit.get("disk_matches_memory") is not True:
            issues.append("final audit did not prove disk/memory parity")
        if audit.get("hard_passed") is not True:
            issues.append("hard publication gate did not pass")
        if (audit.get("publish_gate") or {}).get("passed") is not True:
            issues.append("publish gate did not pass")
        if (audit.get("scenario_contract") or {}).get("valid") is not True:
            issues.append("scenario contract did not pass")

    for label, path, expected in (
        ("report", report_path, audit.get("markdown_sha256")),
        ("forecast", forecast_path, audit.get("forecast_sha256")),
    ):
        if not os.path.isfile(path):
            issues.append(f"{label} artifact is missing")
        elif not expected:
            issues.append(f"{label} audit hash is missing")
        elif _sha256_file(path) != expected:
            issues.append(f"{label} artifact hash does not match the final audit")

    if issues:
        raise RuntimeError(f"{pipeline_id} is not publishable: " + "; ".join(issues))
    return audit


def _validate_namespace(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value or ""):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def _parse_local_markdown_target(target: str) -> tuple[str, str, str] | None:
    """Return (source path, URL suffix, optional title) for a safe local chart link."""
    raw = (target or "").strip()
    if not raw or raw.startswith("#"):
        return None
    target_match = MARKDOWN_TARGET_RE.fullmatch(raw)
    if target_match is None:
        if urlsplit(raw).scheme:
            return None
        raise ValueError(f"unsafe local Markdown asset: {target}")

    url = target_match.group("url")
    title = target_match.group("title") or ""
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or url.startswith("//"):
        return None
    if any(char.isspace() for char in url):
        raise ValueError(f"unsafe local Markdown asset: {target}")

    decoded_path = unquote(parsed.path)
    if not re.fullmatch(r"charts/[A-Za-z0-9._-]+", decoded_path or ""):
        raise ValueError(f"unsafe local Markdown asset: {target}")
    suffix = (f"?{parsed.query}" if parsed.query else "") + (
        f"#{parsed.fragment}" if parsed.fragment else ""
    )
    return decoded_path, suffix, title


def _markdown_asset_paths(markdown: str) -> list[str]:
    paths = []
    seen = set()
    for match in MARKDOWN_LINK_RE.finditer(markdown or ""):
        parsed = _parse_local_markdown_target(match.group("target"))
        if parsed is None:
            continue
        path, _suffix, _title = parsed
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _copy_static_asset(source: str, destination: str) -> None:
    """Copy binary assets byte-for-byte and normalize generated HTML whitespace."""
    if os.path.splitext(source)[1].lower() != ".html":
        shutil.copyfile(source, destination)
        return
    with open(source, encoding="utf-8") as f:
        html = f.read()
    normalized = re.sub(r"[ \t]+(?=\r?$)", "", html, flags=re.MULTILINE)
    with open(destination, "w", encoding="utf-8") as f:
        f.write(normalized)


def copy_markdown_assets(
    markdown: str,
    source_dir: str,
    output_dir: str,
    destination_namespace: str,
) -> list[str]:
    """Copy all safe local chart links into one isolated static-site namespace."""
    namespace = _validate_namespace(destination_namespace, "asset namespace")
    paths = _markdown_asset_paths(markdown)
    output_assets = os.path.join(output_dir, namespace)
    if os.path.isdir(output_assets):
        shutil.rmtree(output_assets)

    source_root = os.path.realpath(source_dir)
    exported = []
    for relative_path in paths:
        source = os.path.realpath(os.path.join(source_root, relative_path))
        if os.path.commonpath([source, source_root]) != source_root:
            raise ValueError(f"unsafe local Markdown asset: {relative_path}")
        if not os.path.isfile(source):
            raise FileNotFoundError(f"referenced Markdown asset is missing: {relative_path}")
        output_relative = f"{namespace}/{os.path.basename(relative_path)}"
        destination = os.path.join(output_dir, output_relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        _copy_static_asset(source, destination)
        exported.append(output_relative)
    return exported


def rebase_markdown_assets(
    markdown: str,
    run_key: str,
    destination_namespace: str,
) -> str:
    """Make local Markdown links resolve relative to docs/demo.html."""
    key = _validate_namespace(run_key, "run key")
    namespace = _validate_namespace(destination_namespace, "asset namespace")

    def _replace(match: re.Match) -> str:
        parsed = _parse_local_markdown_target(match.group("target"))
        if parsed is None:
            return match.group(0)
        path, suffix, title = parsed
        destination = f"demos/{key}/{namespace}/{os.path.basename(path)}{suffix}{title}"
        return f"{match.group('prefix')}{destination}{match.group('suffix')}"

    return MARKDOWN_LINK_RE.sub(_replace, markdown or "")


def copy_report_assets(markdown: str, report_dir: str, output_dir: str) -> list[str]:
    """Copy report-local chart assets, removing stale report charts."""
    return copy_markdown_assets(
        markdown,
        report_dir,
        output_dir,
        destination_namespace="charts",
    )


def rebase_report_assets(markdown: str, run_key: str) -> str:
    """Make fetched report assets resolve relative to docs/demo.html."""
    return rebase_markdown_assets(markdown, run_key, destination_namespace="charts")


def validate_retained_graph(output_dir: str, expected_graph_id: str) -> None:
    """Prevent strict --skip-graph exports from retaining another run's graph."""
    existing_graph = _read_json(os.path.join(output_dir, "graph.json")) or {}
    if existing_graph.get("graph_id") != expected_graph_id:
        raise RuntimeError(
            "--skip-graph cannot publish over stale graph.json "
            f"(expected {expected_graph_id!r}, found {existing_graph.get('graph_id')!r})"
        )


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


def export_run(
    key: str,
    pipeline_id: str,
    skip_graph: bool,
    require_publishable: bool = False,
) -> None:
    state = _read_json(os.path.join(UPLOADS, "pipelines", pipeline_id, "pipeline_state.json"))
    if not state:
        print(f"!! {key}: pipeline state missing, skipped")
        return
    audit = (
        validate_publishable_run(pipeline_id, state)
        if require_publishable
        else _read_json(os.path.join(UPLOADS, "reports", state.get("report_id", ""), "final_audit.json"))
    )
    out = os.path.join(OUT_ROOT, key)
    os.makedirs(out, exist_ok=True)
    handoff = os.path.join(UPLOADS, "pipelines", pipeline_id, "handoff")

    # stage 1 — research log + dossier (+ structured extraction when present)
    log_src = os.path.join(handoff, "research_progress.log")
    if os.path.exists(log_src):
        shutil.copyfile(log_src, os.path.join(out, "research_log.txt"))
    dossier_src = os.path.join(handoff, "research_report.md")
    with open(dossier_src, encoding="utf-8") as f:
        dossier_md = f.read()
    dossier_assets = copy_markdown_assets(
        dossier_md,
        handoff,
        out,
        destination_namespace="research-charts",
    )
    dossier_md = rebase_markdown_assets(
        dossier_md,
        key,
        destination_namespace="research-charts",
    )
    with open(os.path.join(out, "dossier.md"), "w", encoding="utf-8") as f:
        f.write(dossier_md)
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
    exported_graph_id = state["graph_id"]
    if not skip_graph:
        from app.services.graphiti_client import ApiError

        graph_id = state["graph_id"]
        try:
            graph = export_graph(graph_id)
        except ApiError as e:
            if getattr(e, "status_code", None) != 404:
                raise
            print(f"   original graph {graph_id} is gone (404) — rebuilding from dossier")
            graph_id = rebuild_graph(key, out)
            graph = export_graph(graph_id)
        exported_graph_id = graph_id
        graph["graph_id"] = graph_id
        _write_json(os.path.join(out, "graph.json"), graph)
        print(f"   graph: {len(graph['nodes'])} nodes / {len(graph['links'])} edges")
    elif require_publishable:
        validate_retained_graph(out, state["graph_id"])

    # stage 5 — forum feed
    forum = export_forum(os.path.join(UPLOADS, "simulations", state["simulation_id"]))
    _write_json(os.path.join(out, "forum.json"), forum)

    # stage 6 — final report (placeholder sections stripped for presentation)
    report_dir = os.path.join(UPLOADS, "reports", state["report_id"])
    report_md = open(os.path.join(report_dir, "full_report.md"), encoding="utf-8").read()
    cleaned = strip_placeholder_sections(report_md)
    report_assets = copy_report_assets(cleaned, report_dir, out)
    cleaned = rebase_report_assets(cleaned, key)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(cleaned)

    # run metadata for the site cards/header
    run_state = _read_json(os.path.join(UPLOADS, "simulations", state["simulation_id"], "run_state.json")) or {}
    cfg = _read_json(os.path.join(UPLOADS, "simulations", state["simulation_id"], "simulation_config.json")) or {}
    agents = cfg.get("agent_configs") or cfg.get("agents") or []
    artifact_paths = [
        "research_log.txt",
        "dossier.md",
        "actors.json",
        "sources.json",
        "ontology.json",
        "graph.json",
        "forum.json",
        "report.md",
        *dossier_assets,
        *report_assets,
    ]
    artifact_sha256 = {
        path: _sha256_file(os.path.join(out, path))
        for path in artifact_paths
        if os.path.isfile(os.path.join(out, path))
    }
    _write_json(os.path.join(out, "meta.json"), {
        "pipeline_id": pipeline_id,
        "report_id": state.get("report_id"),
        "simulation_id": state.get("simulation_id"),
        "graph_id": exported_graph_id,
        "status": state.get("status"),
        "prompt": state.get("prompt", ""),
        "created_at": state.get("created_at", ""),
        "mode": state.get("mode", "full"),
        "rounds": run_state.get("total_rounds"),
        "personas": len(agents) if isinstance(agents, list) else None,
        "has_actors": os.path.exists(os.path.join(handoff, "actors.json")),
        "dossier_assets": dossier_assets,
        "report_assets": report_assets,
        "artifact_sha256": artifact_sha256,
        "publication": {
            "hard_passed": audit.get("hard_passed"),
            "publish_passed": (audit.get("publish_gate") or {}).get("passed"),
            "scenario_contract_valid": (audit.get("scenario_contract") or {}).get("valid"),
            "citation_coverage": (audit.get("citation_grounding") or {}).get("resolved_coverage"),
            "semantic_citations_passed": (audit.get("semantic_citations") or {}).get("passed"),
            "markdown_sha256": audit.get("markdown_sha256"),
            "forecast_sha256": audit.get("forecast_sha256"),
        } if isinstance(audit, dict) else None,
    })
    print(f"ok {key}: exported -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-graph", action="store_true", help="skip the Zep graph export (no network)")
    ap.add_argument("--only", help="export a single run key")
    ap.add_argument(
        "--require-publishable",
        action="store_true",
        help="fail unless the pipeline and final read-only publication audit pass",
    )
    args = ap.parse_args()

    runs = {args.only: RUNS[args.only]} if args.only else RUNS
    for key, pid in runs.items():
        export_run(
            key,
            pid,
            skip_graph=args.skip_graph,
            require_publishable=args.require_publishable,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
