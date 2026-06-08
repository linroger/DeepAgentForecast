#!/usr/bin/env python3
"""
Headless driver for the MiroFish web pipeline.

Drives the Flask backend API end-to-end:
  ontology/generate -> graph/build -> simulation/create -> prepare -> start -> report/generate

Usage:
  python run_simulation.py --files world2026/world_scenario_dim01.md \
      --requirement "How will global publics react to US-China Taiwan tensions over 2 weeks?" \
      --max-rounds 3 --platform parallel

Kept deliberately small by default because the claude-cli provider is slow/expensive
(~30-40s and real subscription cost per LLM call).
"""
import argparse
import sys
import time
import json
import os

import requests

BASE = os.environ.get("MIROFISH_BASE", "http://localhost:5001")


def _post(path, **kw):
    r = requests.post(BASE + path, timeout=kw.pop("timeout", 120), **kw)
    return r


def _get(path, **kw):
    r = requests.get(BASE + path, timeout=kw.pop("timeout", 120), **kw)
    return r


def _ok(r, label):
    try:
        data = r.json()
    except Exception:
        print(f"[FAIL] {label}: HTTP {r.status_code} non-JSON: {r.text[:300]}")
        sys.exit(1)
    if r.status_code != 200 or not data.get("success", True):
        print(f"[FAIL] {label}: HTTP {r.status_code}: {json.dumps(data, ensure_ascii=False)[:500]}")
        sys.exit(1)
    return data


def poll(path, *, done_when, label, interval=10, max_wait=7200, body=None, method="GET"):
    """Poll an endpoint until done_when(data) is truthy; returns the final data."""
    start = time.time()
    last = ""
    while True:
        if method == "GET":
            r = _get(path)
        else:
            r = _post(path, json=body or {})
        try:
            data = r.json()
        except Exception:
            data = {"_raw": r.text[:200]}
        status = json.dumps(data.get("data", data), ensure_ascii=False)[:160]
        if status != last:
            print(f"  [{label}] {int(time.time()-start)}s: {status}")
            last = status
        try:
            if done_when(data):
                return data
        except Exception:
            pass
        if time.time() - start > max_wait:
            print(f"[TIMEOUT] {label} after {max_wait}s")
            sys.exit(1)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--requirement", required=True)
    ap.add_argument("--project-name", default="World2026 Sim")
    ap.add_argument("--platform", default="parallel", choices=["parallel", "twitter", "reddit"])
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--enable-twitter", default="true")
    ap.add_argument("--enable-reddit", default="true")
    args = ap.parse_args()

    print(f"== MiroFish headless run against {BASE} ==")

    # health
    try:
        h = _get("/api/health", timeout=10)
        print("health:", h.status_code, h.text[:120])
    except Exception as e:
        print("health check skipped/failed:", e)

    # 1) ontology/generate (multipart)
    print("\n[1/6] ontology/generate ...")
    files = []
    fhs = []
    for p in args.files:
        fh = open(p, "rb")
        fhs.append(fh)
        files.append(("files", (os.path.basename(p), fh, "text/markdown")))
    form = {
        "simulation_requirement": args.requirement,
        "project_name": args.project_name,
    }
    r = _post("/api/graph/ontology/generate", data=form, files=files, timeout=900)
    for fh in fhs:
        fh.close()
    data = _ok(r, "ontology/generate")["data"]
    project_id = data["project_id"]
    et = data.get("ontology", {}).get("entity_types", [])
    print(f"  project_id={project_id}  entity_types={len(et)}  text_len={data.get('total_text_length')}")

    # 2) graph/build (async -> task)
    print("\n[2/6] graph/build ...")
    r = _post("/api/graph/build", json={"project_id": project_id})
    bdata = _ok(r, "graph/build")["data"]
    task_id = bdata.get("task_id")
    print(f"  task_id={task_id}")
    poll(f"/api/graph/task/{task_id}",
         label="build",
         done_when=lambda d: (d.get("data", {}).get("status") in ("completed", "failed", "success")),
         interval=8, max_wait=3600)
    # fetch graph_id from project
    pr = _get(f"/api/graph/project/{project_id}")
    pdata = _ok(pr, "project")["data"]
    graph_id = pdata.get("graph_id")
    print(f"  graph_id={graph_id}")
    if not graph_id:
        print("[FAIL] no graph_id after build"); sys.exit(1)

    # 3) simulation/create
    print("\n[3/6] simulation/create ...")
    r = _post("/api/simulation/create", json={
        "project_id": project_id, "graph_id": graph_id,
        "enable_twitter": args.enable_twitter == "true",
        "enable_reddit": args.enable_reddit == "true",
    })
    sdata = _ok(r, "simulation/create")["data"]
    simulation_id = sdata["simulation_id"]
    print(f"  simulation_id={simulation_id}")

    # 4) simulation/prepare (async)
    print("\n[4/6] simulation/prepare ...")
    r = _post("/api/simulation/prepare", json={"simulation_id": simulation_id})
    _ok(r, "simulation/prepare")
    poll("/api/simulation/prepare/status",
         method="POST", body={"simulation_id": simulation_id},
         label="prepare",
         done_when=lambda d: (d.get("data", {}).get("status") in ("ready", "completed", "failed")),
         interval=10, max_wait=5400)

    # 5) simulation/start
    print(f"\n[5/6] simulation/start (platform={args.platform}, max_rounds={args.max_rounds}) ...")
    r = _post("/api/simulation/start", json={
        "simulation_id": simulation_id,
        "platform": args.platform,
        "max_rounds": args.max_rounds,
    })
    _ok(r, "simulation/start")
    poll(f"/api/simulation/{simulation_id}/run-status",
         label="run",
         done_when=lambda d: (d.get("data", {}).get("runner_status") in ("completed", "stopped", "failed", "finished")),
         interval=15, max_wait=14400)

    # 6) report/generate
    print("\n[6/6] report/generate ...")
    r = _post("/api/report/generate", json={"simulation_id": simulation_id})
    rep = _ok(r, "report/generate")
    report_id = rep.get("data", {}).get("report_id")
    print(f"  report task started, report_id={report_id}")
    if report_id:
        poll(f"/api/report/{report_id}/progress",
             label="report",
             done_when=lambda d: (d.get("data", {}).get("status") in ("completed", "failed", "done")),
             interval=15, max_wait=7200)
        final = _get(f"/api/report/{report_id}")
        print("\n== REPORT ==")
        print(json.dumps(final.json(), ensure_ascii=False, indent=2)[:4000])

    print(f"\n== DONE ==  project={project_id} graph={graph_id} simulation={simulation_id} report={report_id}")


if __name__ == "__main__":
    main()
