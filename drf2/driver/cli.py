"""CLI entry point.

    python -m drf2.driver.cli run    --question "..." [--config drf2/config/config.yaml]
                                     [--pipeline-id X] [--seeds 1,2,3] [--dry-run]
    python -m drf2.driver.cli resume --pipeline-id X [--config ...] [--dry-run]
    python -m drf2.driver.cli status --pipeline-id X [--config ...]

Config: reads the ``driver:`` section of the shared drf2 config.yaml (the rest of the
file is the harness's own schema and is ignored here). Missing file/section → defaults;
a live run additionally needs driver.harness.base_url. ``--dry-run`` exercises the full
pipeline offline against built-in fixture artifacts (no gateway, no sim engine, no LLM).
Exit codes: 0 = completed, 1 = pipeline failed, 2 = usage/config error.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from .harness_client import DryRunHarness, DrySimEngine, HttpSimEngine, RunsApiHarness
from .pipeline import PipelineDriver
from .state import StateStore

DEFAULT_BASE_DIR = ".drf2/pipelines"


def _load_driver_config(path: Optional[str]) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        print(f"config not found: {path} (using defaults)", file=sys.stderr)
        return {}
    import yaml
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SystemExit(f"config parse error in {path}: {e}")
    section = data.get("driver")
    return section if isinstance(section, dict) else {}


# ---------------------------------------------------------------------------
# Offline dry-run fixtures: plausible artifacts that pass every gate, so the
# whole state machine + manifest + gate path can be smoke-tested with zero deps.
# ---------------------------------------------------------------------------

def _subdir_from_prompt(prompt: str) -> str:
    marker = "/mnt/user-data/outputs/"
    for line in prompt.splitlines():
        if marker in line and "deliverables" in line:
            cand = line.split(marker, 1)[1].split("/", 1)[0].strip()
            if cand and " " not in cand:
                return cand
    return ""


def _dry_fixture_forecast() -> dict[str, Any]:
    probs = [0.85, 0.8, 0.75, 0.72, 0.25, 0.2, 0.15, 0.55, 0.62, 0.35]
    return {
        "headline": "dry-run fixture forecast",
        "scenarios": [
            {"name": "Scenario A", "probability": 0.6, "key_drivers": ["d1"],
             "resolution_criteria": "metric X > 10 by 2027-01-01"},
            {"name": "Scenario B", "probability": 0.4, "key_drivers": ["d2"],
             "resolution_criteria": "metric X <= 10 by 2027-01-01"},
        ],
        "binary_forecasts": [
            {"statement": f"claim {i}", "probability": p, "criteria_sharp": True,
             "resolution_criteria": f"indicator {i} crosses {i * 10} by 2027-06-30"}
            for i, p in enumerate(probs, 1)
        ],
    }


def _dry_effects() -> dict[str, Any]:
    def _write(ws: Path, rel: str, text: str) -> None:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def research(ws: Path, prompt: str) -> None:
        _write(ws, "research_report.md", "# Research\n" + ("evidence line\n" * 60))
        _write(ws, "meta.json", json.dumps({"research_quality": {"score": 0.9}}))

    def ontology(ws: Path, prompt: str) -> None:
        _write(ws, "ontology.json", json.dumps({"entity_types": ["Actor"], "actors": []}))

    def graph(ws: Path, prompt: str) -> None:
        _write(ws, "kg_build_summary.json", json.dumps({"episodes": 12, "graph_id": "dry"}))

    def prepare(ws: Path, prompt: str) -> None:
        _write(ws, "simulation_config.json", json.dumps({"num_agents": 8, "rounds": 3}))

    def report(ws: Path, prompt: str) -> None:
        sub = _subdir_from_prompt(prompt)
        base = f"{sub}/" if sub else ""
        _write(ws, f"{base}full_report.md", "# Forecast Report\n" + ("analysis paragraph\n" * 200))
        _write(ws, f"{base}forecast.json", json.dumps(_dry_fixture_forecast()))

    return {"research": research, "ontology": ontology, "graph": graph,
            "prepare": prepare, "report": report}


def _dry_sim_engine() -> DrySimEngine:
    return DrySimEngine(statuses=[
        {"completed": False, "current_round": 1},
        {"completed": True, "current_round": 3, "total_rounds": 3,
         "run_summary": {"organic_action_count": 42, "simulation_health": "ok"}},
    ])


# ---------------------------------------------------------------------------


def _build_driver(dcfg: dict[str, Any], store: StateStore, *, dry_run: bool,
                  thread_id: str) -> PipelineDriver:
    if dry_run:
        workspace = Path(store.base_dir) / "dry-workspace" / thread_id
        harness: Any = DryRunHarness(workspace=workspace, stage_effects=_dry_effects(),
                                     thread_id=thread_id)
        sim_engine: Any = _dry_sim_engine()
        sim_cfg = {"interval_s": 0, "timeout_s": 60, "stall_s": 30}
    else:
        hcfg = dcfg.get("harness") or {}
        base_url = hcfg.get("base_url")
        if not base_url:
            raise SystemExit("driver.harness.base_url missing in config (required for live runs)")
        harness = RunsApiHarness(
            base_url=base_url, thread_id=thread_id,
            user_id=hcfg.get("user_id", "default"),
            workspace_root=hcfg.get("workspace_root"),
            api_key=hcfg.get("api_key"),
            poll_interval_s=float(hcfg.get("poll_interval_s", 5.0)))
        scfg = dcfg.get("sim_engine") or {}
        sim_engine = HttpSimEngine(scfg["base_url"]) if scfg.get("base_url") else None
        sim_cfg = dcfg.get("sim") or {}
    ecfg = dcfg.get("ensemble") or {}
    return PipelineDriver(store, harness, sim_engine,
                          gate_config=dcfg.get("gates"),
                          stage_timeouts_s=dcfg.get("stage_timeouts_s"),
                          sim_config=sim_cfg,
                          extremize_a=ecfg.get("extremize_a"))


def _print_summary(state) -> int:
    out = {"pipeline_id": state.pipeline_id, "status": state.status, "error": state.error,
           "stages": {n: {"status": s.status, "health": s.health, "reused": s.reused,
                          "issues": s.issues}
                      for n, s in state.stages.items()}}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if state.status == "completed" else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="drf2.driver", description="DRF-2 pipeline driver")
    parser.add_argument("--config", default="drf2/config/config.yaml")
    parser.add_argument("--base-dir", default=None, help="pipeline state root (default driver.base_dir)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run a new pipeline")
    p_run.add_argument("--question", required=True)
    p_run.add_argument("--pipeline-id", default=None)
    p_run.add_argument("--seeds", default=None, help="comma-separated ints → ensemble fan-out")
    p_run.add_argument("--dry-run", action="store_true")

    p_res = sub.add_parser("resume", help="resume an existing pipeline")
    p_res.add_argument("--pipeline-id", required=True)
    p_res.add_argument("--dry-run", action="store_true")

    p_st = sub.add_parser("status", help="print pipeline state")
    p_st.add_argument("--pipeline-id", required=True)

    args = parser.parse_args(argv)
    try:
        dcfg = _load_driver_config(args.config)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    store = StateStore(args.base_dir or dcfg.get("base_dir") or DEFAULT_BASE_DIR)

    if args.command == "status":
        if not store.exists(args.pipeline_id):
            print(f"pipeline not found: {args.pipeline_id}", file=sys.stderr)
            return 2
        print(json.dumps(store.load(args.pipeline_id).to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "run":
        pid = args.pipeline_id or uuid.uuid4().hex[:12]
        seeds = None
        if args.seeds:
            try:
                seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
            except ValueError:
                print(f"invalid --seeds: {args.seeds}", file=sys.stderr)
                return 2
        thread_id = (dcfg.get("harness") or {}).get("thread_id") or f"drf2-{pid}"
        try:
            driver = _build_driver(dcfg, store, dry_run=args.dry_run, thread_id=thread_id)
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 2
        state = driver.run(args.question, pipeline_id=pid, seeds=seeds)
        return _print_summary(state)

    # resume
    if not store.exists(args.pipeline_id):
        print(f"pipeline not found: {args.pipeline_id}", file=sys.stderr)
        return 2
    prior = store.load(args.pipeline_id)
    thread_id = prior.thread_id or f"drf2-{args.pipeline_id}"
    try:
        driver = _build_driver(dcfg, store, dry_run=args.dry_run, thread_id=thread_id)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    state = driver.resume(args.pipeline_id)
    return _print_summary(state)


if __name__ == "__main__":
    sys.exit(main())
