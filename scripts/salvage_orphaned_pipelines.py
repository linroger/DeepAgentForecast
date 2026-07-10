#!/usr/bin/env python3
"""W9-1: 打捞被 reconcile_orphans 误判为 failed 的「报告已完整落盘」管线。

背景：后端重启的孤儿回收此前把所有 running 孤儿一律标 failed——即便中断发生在主报告
完成之后的多种子集成/收尾窗口。pipe_0f2bee7bd649 与 pipe_a8986bffd918 两条 $10+ 的跑
正是这样带着完整的 full_report.md + forecast.json 被整线判死的。编排器侧的运行时打捞
（PipelineOrchestrator.reconcile_orphans + PipelineManager.mark_salvaged_completed）只
作用于「下次启动时仍是 running 的孤儿」；本脚本对**已经落成 failed 的历史状态文件**
应用同一条打捞规则，把它们改写为 completed（pipeline_health=degraded，集成按已跳过）。

打捞判据（与 PipelineOrchestrator._orphan_completed_report 一致，全部满足才打捞）：
  1. pipeline_state.json 的 status 为 failed（--include-cancelled 时含 cancelled）；
  2. state.report_id 存在；
  3. stages.report.status == 'completed'（报告链自己宣布过成功）；
  4. 报告目录（<reports_dir>/<report_id>/）下 full_report.md 非空
     且 forecast.json 可解析为非空 JSON object。

默认 DRY-RUN：只打印会被打捞的管线，不改任何文件；加 ``--apply`` 才真正改写。
本脚本刻意零依赖（不 import 后端 app 包）——可在任何 Python 3.9+ 下直接运行，
状态改写字段与 PipelineManager.mark_salvaged_completed 保持一致（改一处须同步另一处）。

用法：
    python scripts/salvage_orphaned_pipelines.py                 # dry-run（默认路径）
    python scripts/salvage_orphaned_pipelines.py --apply         # 真正改写
    python scripts/salvage_orphaned_pipelines.py \
        --pipelines-dir backend/uploads/pipelines \
        --reports-dir backend/uploads/reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

SALVAGE_NOTE = ("后端在多种子集成/收尾期间被中断；主报告已完整落盘，"
                "管线按 completed(degraded) 打捞恢复，多种子集成已跳过。")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str) -> Optional[Any]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — 损坏文件按缺失处理
        return None


def _write_json_atomic(path: str, data: Any) -> None:
    """tmp + os.replace 原子写（与后端 utils.atomic.write_json_atomic 同语义）。"""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".salvage-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def eligible_report(data: dict, reports_dir: str) -> Optional[dict]:
    """打捞判据（镜像 PipelineOrchestrator._orphan_completed_report，改动须两处同步）。

    满足 → {'report_id', 'report_dir'}；否则 None。
    """
    if not isinstance(data, dict):
        return None
    report_id = data.get("report_id")
    if not report_id or not isinstance(report_id, str):
        return None
    if os.sep in report_id or (os.altsep and os.altsep in report_id) or ".." in report_id:
        return None  # 防路径逃逸：report_id 必须是纯目录名
    st = (data.get("stages") or {}).get("report")
    if not (isinstance(st, dict) and st.get("status") == "completed"):
        return None
    report_dir = os.path.join(reports_dir, report_id)
    fr = os.path.join(report_dir, "full_report.md")
    try:
        if not (os.path.isfile(fr) and os.path.getsize(fr) > 0):
            return None
    except OSError:
        return None
    fc = _read_json(os.path.join(report_dir, "forecast.json"))
    if not (isinstance(fc, dict) and fc):
        return None
    return {"report_id": report_id, "report_dir": report_dir}


def salvage_state(data: dict, note: str = SALVAGE_NOTE) -> dict:
    """就地把一份 failed/cancelled 状态 dict 改写为 completed(degraded)（并返回它）。

    字段与 PipelineManager.mark_salvaged_completed 保持一致（改一处须同步另一处）。
    """
    data["status"] = "completed"
    data["error"] = None
    data["global_progress"] = 100
    data["updated_at"] = _utcnow()
    opts = data.get("options")
    if not isinstance(opts, dict):
        opts = {}
        data["options"] = opts
    ph = opts.get("pipeline_health")
    if not isinstance(ph, dict):
        ph = {"status": "degraded", "issues": []}
    elif ph.get("status") == "ok":
        ph["status"] = "degraded"
    ph.setdefault("status", "degraded")
    issues = ph.get("issues")
    if not isinstance(issues, list):
        issues = []
    if note not in issues:
        issues.append(note)
    ph["issues"] = issues
    opts["pipeline_health"] = ph
    opts["ensemble_done"] = True
    opts["ensemble_skipped"] = note
    opts["salvaged_orphan_at"] = _utcnow()
    stages = data.get("stages") or {}
    rs = stages.get("report")
    if isinstance(rs, dict):
        rs["status"] = "completed"
        rs["progress"] = 100
        rs["error"] = None
        if not rs.get("finished_at"):
            rs["finished_at"] = _utcnow()
        if "多种子集成" in str(rs.get("message") or ""):
            rs["message"] = "报告完成（多种子集成被中断，已跳过）"
    data["current_stage"] = "report"
    return data


def find_candidates(pipelines_dir: str, reports_dir: str,
                    include_cancelled: bool = False) -> list[dict]:
    """扫描 pipelines_dir 下所有 pipeline_state.json，返回可打捞清单（确定性排序）。"""
    statuses = {"failed"} | ({"cancelled"} if include_cancelled else set())
    out: list[dict] = []
    if not os.path.isdir(pipelines_dir):
        return out
    for pid in sorted(os.listdir(pipelines_dir)):
        state_path = os.path.join(pipelines_dir, pid, "pipeline_state.json")
        data = _read_json(state_path)
        if not isinstance(data, dict):
            continue
        if data.get("status") not in statuses:
            continue
        arts = eligible_report(data, reports_dir)
        if arts is None:
            continue
        out.append({
            "pipeline_id": data.get("pipeline_id") or pid,
            "status": data.get("status"),
            "report_id": arts["report_id"],
            "report_dir": arts["report_dir"],
            "state_path": state_path,
            "error": (data.get("error") or "")[:120],
        })
    return out


def run(pipelines_dir: str, reports_dir: str, *, apply: bool = False,
        include_cancelled: bool = False, out=sys.stdout) -> list[dict]:
    """执行扫描（+ 可选改写），返回候选清单。dry-run（apply=False）绝不写任何文件。"""
    candidates = find_candidates(pipelines_dir, reports_dir,
                                 include_cancelled=include_cancelled)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] pipelines_dir={pipelines_dir}", file=out)
    print(f"[{mode}] reports_dir={reports_dir}", file=out)
    if not candidates:
        print(f"[{mode}] 无可打捞管线（status=failed"
              f"{'/cancelled' if include_cancelled else ''} 且报告完整在盘）。", file=out)
        return candidates
    for c in candidates:
        print(f"[{mode}] {c['pipeline_id']}: {c['status']} → completed(degraded)"
              f"  report={c['report_id']}  error=「{c['error']}」", file=out)
        if apply:
            data = _read_json(c["state_path"])
            if not isinstance(data, dict):
                print(f"[{mode}]   !! 状态文件读回失败，跳过: {c['state_path']}", file=out)
                continue
            if data.get("status") == "running":
                # 竞态护栏：扫描后有进程把它复活了 → 绝不触碰 running 状态。
                print(f"[{mode}]   !! 已变为 running（有进程在跑），跳过", file=out)
                continue
            _write_json_atomic(c["state_path"], salvage_state(data))
            print(f"[{mode}]   已改写 {c['state_path']}", file=out)
    if not apply:
        print(f"[{mode}] 共 {len(candidates)} 条可打捞。加 --apply 真正改写。", file=out)
    else:
        print(f"[{mode}] 完成：改写 {len(candidates)} 条。", file=out)
    return candidates


def _default_dirs() -> tuple[str, str]:
    """默认路径：<repo>/backend/uploads/{pipelines,reports}（以脚本位置为锚）。"""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    return (os.path.join(repo_root, "backend", "uploads", "pipelines"),
            os.path.join(repo_root, "backend", "uploads", "reports"))


def main(argv: Optional[list[str]] = None) -> int:
    dp, dr = _default_dirs()
    p = argparse.ArgumentParser(
        description="打捞「报告已完整落盘却被整线判 failed」的管线（默认 dry-run）。")
    p.add_argument("--pipelines-dir", default=dp, help=f"pipelines 目录（默认 {dp}）")
    p.add_argument("--reports-dir", default=dr, help=f"reports 目录（默认 {dr}）")
    p.add_argument("--apply", action="store_true", help="真正改写状态文件（默认只打印）")
    p.add_argument("--include-cancelled", action="store_true",
                   help="也打捞 status=cancelled 的管线（默认仅 failed）")
    args = p.parse_args(argv)
    run(args.pipelines_dir, args.reports_dir, apply=args.apply,
        include_cancelled=args.include_cancelled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
