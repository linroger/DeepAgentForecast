"""Deterministic health gates — port the DECISIONS of pipeline_orchestrator's S1 gate
suite, not its code. Every gate reads artifacts/engine state directly from disk (never
via LLM) and returns a typed verdict:

  * ``fail``     — deliverable is broken; the driver marks the stage/pipeline failed
                   (legacy: 8/13 runs reported completed/100% with broken deliverables);
  * ``degraded`` — usable but must be caveated (hollow sim, hedged binaries, …);
  * ``pass``     — artifact verified.

Decision provenance (backend/app/services/pipeline_orchestrator.py):
  research floor   → stage-1 report guard (~L820-845) + _surface_research_quality (~L3054);
  hollow-sim gate  → _assess_run_health (~L2464): degraded, never hard-fail;
  binary conviction→ forecast_extractor._binary_quality (stdev>=0.12, count>=min, …),
                     surfaced as a degraded signal in _assess_report_health;
  deliverable      → _assess_report_health (~L2387): hard-fail on all-placeholder /
                     missing forecast / trivially-short report.
"""

from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .legacy import ensure_backend_importable

ensure_backend_importable()
# 唯一真源：信念/客观性记分牌直接复用报告侧实现（stdev>=0.12、midband、conviction、sharp）。
from app.services.forecast_extractor import _binary_quality  # noqa: E402


@dataclass
class GateResult:
    name: str
    status: str  # pass | degraded | fail
    issues: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def hard_fail(self) -> bool:
        return self.status == "fail"


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _read_json(path: str) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# RES-1/RES-8：LLM 降级/错误串标记（与 pipeline_orchestrator._LLM_ERROR_MARKERS 同源；
# 常量复制而非 import——导入 4.2K 编排器会拖进重型服务依赖，违背 driver 轻量原则）。
LLM_ERROR_MARKERS = (
    "The configured LLM provider",   # DeerFlow LLMErrorHandlingMiddleware 降级文案
    "LLM request failed",             # 原始 provider 报错被当成正文
    "unprocessable_entity",           # 例如 MiniMax 422 内容审核
    "new_sensitive",                  # MiniMax 域内容过滤命中(code 1026)
    "Error code: 4", "Error code: 5",  # 4xx/5xx 错误串
)


def research_gate(handoff_dir: str, *, min_report_chars: int = 400,
                  quality_floor: float = 0.0) -> GateResult:
    """研究质量地板：空/过短/错误串报告 → fail；卷宗降级、记分牌低于地板 → degraded。"""
    issues: list[str] = []
    meta: dict[str, Any] = {}
    report = _read_text(os.path.join(handoff_dir, "research_report.md"))
    rlen = len(report.strip())
    meta["report_chars"] = rlen
    if rlen == 0:
        return GateResult("research", "fail", ["research_report.md missing or empty"], meta)
    if min_report_chars and rlen < min_report_chars:
        return GateResult(
            "research", "fail",
            [f"research report only {rlen} chars (< floor {min_report_chars})"], meta)
    if rlen < 400 and any(m in report for m in LLM_ERROR_MARKERS):
        return GateResult(
            "research", "fail",
            ["research report is an LLM degradation/error message, not research"], meta)

    # RES-8：卷宗含错误串或过短 → 按缺失处理（降级信号，不硬失败——单轨仍可继续）。
    dossier = _read_text(os.path.join(handoff_dir, "actor_dossier.md"))
    if dossier.strip():
        dlen = len(dossier.strip())
        if (min_report_chars and dlen < min_report_chars) or any(m in dossier for m in LLM_ERROR_MARKERS):
            issues.append("actor_dossier.md is a degraded artifact — treat as missing downstream")
            meta["dossier_degraded"] = True

    # I-0-3：research_quality 记分牌低于地板 → 软告警（继续，不硬失败——GIGO 是软信号）。
    rq_meta = _read_json(os.path.join(handoff_dir, "meta.json"))
    rq = (rq_meta or {}).get("research_quality") if isinstance(rq_meta, dict) else None
    if isinstance(rq, dict):
        score = rq.get("score")
        meta["research_quality_score"] = score
        if quality_floor > 0 and isinstance(score, (int, float)) and score < quality_floor:
            issues.append(f"research quality score {round(float(score), 3)} < floor {quality_floor}")
    return GateResult("research", "degraded" if issues else "pass", issues, meta)


def hollow_sim_gate(sim_dir: Optional[str] = None, *,
                    summary: Optional[dict[str, Any]] = None,
                    run_state: Optional[dict[str, Any]] = None) -> GateResult:
    """空心模拟门：0 有机行动/errored/truncated → degraded（决策：永不硬失败——报告
    仍可产出，但必须把模拟标记为不可引用的证据）。

    XRUN-11 口径：run_summary.json 的 organic_action_count（种子/有机诚实拆分）为准。
    可传目录（读 run_summary.json / run_state.json）或直接传引擎返回的 dict。
    """
    issues: list[str] = []
    meta: dict[str, Any] = {}
    if summary is None and sim_dir:
        loaded = _read_json(os.path.join(sim_dir, "run_summary.json"))
        summary = loaded if isinstance(loaded, dict) else None
    if run_state is None and sim_dir:
        loaded = _read_json(os.path.join(sim_dir, "run_state.json"))
        run_state = loaded if isinstance(loaded, dict) else None

    organic: Optional[int] = None
    if isinstance(summary, dict):
        oc = summary.get("organic_action_count")
        if isinstance(oc, int):
            organic = oc
            meta["organic_actions"] = oc
        sh = summary.get("simulation_health")
        if isinstance(sh, str) and sh:
            meta["simulation_health"] = sh
            if sh in ("hollow", "errored", "llm_degraded", "truncated"):
                issues.append(f"run_summary simulation_health={sh}")
    else:
        issues.append("run_summary.json missing — simulation outcome unverifiable")

    if isinstance(run_state, dict):
        err = run_state.get("error")
        if err:
            meta["error"] = str(err)[:160]
            issues.append(f"simulation error: {str(err)[:120]}")
        cr = run_state.get("current_round")
        tr = run_state.get("total_rounds") or run_state.get("total_simulation_rounds")
        if isinstance(cr, int) and isinstance(tr, int) and tr > 0 and cr < tr:
            meta["truncated"] = True
            issues.append("simulation was truncated before completion")

    if organic == 0:
        issues.append("simulation produced 0 organic posts/comments (hollow) — "
                      "report must not cite it as evidence")
    return GateResult("run", "degraded" if issues else "pass", issues, meta)


def binary_conviction_gate(forecast_path: str, *, min_count: int = 10) -> GateResult:
    """二元预测信念门（QUALITY-OPT A3/A4）：stdev>=0.12 + count>=min_count（外加
    midband/conviction/sharp-criteria 判据，复用 forecast_extractor._binary_quality）。
    决策口径与编排器一致：未通过 = degraded 信号（硬失败由 deliverable_gate 负责）。
    """
    fc = _read_json(forecast_path)
    if not isinstance(fc, dict):
        return GateResult("binary_conviction", "degraded",
                          ["forecast.json missing/unparsable — binary gate not evaluable"], {})
    raw = fc.get("binary_forecasts")
    binaries: list[dict[str, Any]] = []
    for b in (raw or []):
        if not isinstance(b, dict):
            continue
        try:
            p = float(b.get("probability"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(p) and 0.0 <= p <= 1.0:
            binaries.append({**b, "probability": p})
    if not binaries:
        return GateResult("binary_conviction", "degraded",
                          ["no binary forecasts present"], {"count": 0})
    q = _binary_quality(binaries, min_count=min_count)
    status = "pass" if q.get("passed") else "degraded"
    return GateResult("binary_conviction", status, list(q.get("issues") or []), q)


def deliverable_gate(report_dir: str, *, structured_forecast_required: bool = True,
                     min_report_bytes: int = 2000) -> GateResult:
    """交付物验收（_assess_report_health 决策）：全占位章节 / forecast.json 缺失或空 /
    full_report.md 过短 → HARD FAIL；部分占位章节 → degraded。"""
    issues: list[str] = []
    secs = sorted(glob.glob(os.path.join(report_dir, "section_*.md")))
    total = len(secs)
    placeholder = 0
    for s in secs:
        body = _read_text(s)
        if ("生成失败" in body) or ("本章节" in body and "失败" in body) or len(body.strip()) < 200:
            placeholder += 1

    fc = _read_json(os.path.join(report_dir, "forecast.json"))
    fc_ok = isinstance(fc, dict) and bool(fc.get("scenarios") or fc.get("binary_forecasts"))

    fr_path = os.path.join(report_dir, "full_report.md")
    fr_len = os.path.getsize(fr_path) if os.path.exists(fr_path) else 0

    meta = {"sections": total, "placeholder_sections": placeholder,
            "forecast_ok": fc_ok, "full_report_bytes": fr_len}
    hard = False
    if total and placeholder >= total:
        hard = True
        issues.append(f"all {total} report sections are failure placeholders")
    elif placeholder:
        issues.append(f"{placeholder}/{total} report sections are failure placeholders")
    if not fc_ok and structured_forecast_required:
        hard = True
        issues.append("forecast.json missing or empty (no scenarios/binary_forecasts)")
    if fr_len == 0:
        hard = True
        issues.append("full_report.md missing")
    elif fr_len < min_report_bytes:
        hard = True
        issues.append(f"full_report.md is only {fr_len} bytes (effectively empty)")
    status = "fail" if hard else ("degraded" if issues else "pass")
    return GateResult("deliverable", status, issues, meta)
