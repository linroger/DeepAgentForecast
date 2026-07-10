"""Wave 9（编排器组）：孤儿打捞、集成语义对齐、多轨卷宗整理、遥测增量落盘、轨存活判定。

全部为进程内单元测试，不发任何网络请求、不触碰真实 uploads/。
"""

import importlib.util
import io
import json
import os

import pytest

from app.config import Config
from app.services import ensemble as EN
from app.services.pipeline_orchestrator import (
    PipelineManager,
    PipelineOrchestrator,
    PipelineState,
    _track_artifacts_survived,
    demote_track_headings,
    extract_exec_summary,
    merge_track_reports,
    reconcile_track_reports,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------- helpers
def _state_dict(status="failed", report_status="completed", report_id="report_w9abc"):
    return {
        "pipeline_id": "pipe_wave9test",
        "prompt": "q",
        "schema_version": 2,
        "status": status,
        "report_id": report_id,
        "stages": {"report": {"name": "report", "status": report_status, "progress": 100}},
        "options": {},
    }


def _fc(scs):
    """scs = [(name, probability, resolution_criteria), ...] → forecast dict。"""
    return {
        "headline": "h", "horizon": "2030",
        "scenarios": [
            {"name": n, "probability": p, "key_drivers": [], "resolution_criteria": c}
            for n, p, c in scs
        ],
    }


class _StubLLM:
    """chat() 返回固定文本或抛异常的桩（reconcile_track_reports 用）。"""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


# ---------------------------------------------------------------- W9-1 打捞判定
def test_orphan_completed_report_requires_all_artifacts(tmp_path):
    rd = tmp_path / "report_w9abc"
    rd.mkdir()
    data = _state_dict()
    # 报告目录空 → 不打捞
    assert PipelineOrchestrator._orphan_completed_report(data, report_dir=str(rd)) is None
    (rd / "full_report.md").write_text("# 报告正文", encoding="utf-8")
    # 缺 forecast.json → 不打捞
    assert PipelineOrchestrator._orphan_completed_report(data, report_dir=str(rd)) is None
    (rd / "forecast.json").write_text(
        json.dumps({"scenarios": [{"name": "A", "probability": 1.0}]}), encoding="utf-8")
    got = PipelineOrchestrator._orphan_completed_report(data, report_dir=str(rd))
    assert got == {"report_id": "report_w9abc", "report_dir": str(rd)}


def test_orphan_completed_report_rejects_incomplete_states(tmp_path):
    rd = tmp_path / "report_w9abc"
    rd.mkdir()
    (rd / "full_report.md").write_text("# 报告正文", encoding="utf-8")
    (rd / "forecast.json").write_text(json.dumps({"scenarios": [1]}), encoding="utf-8")
    # report 阶段未完成 → 不打捞（报告链没宣布过成功）
    running = _state_dict(report_status="running")
    assert PipelineOrchestrator._orphan_completed_report(running, report_dir=str(rd)) is None
    # 无 report_id → 不打捞
    no_rid = _state_dict(report_id=None)
    assert PipelineOrchestrator._orphan_completed_report(no_rid, report_dir=str(rd)) is None
    # forecast.json 为空 object → 不打捞
    (rd / "forecast.json").write_text("{}", encoding="utf-8")
    assert PipelineOrchestrator._orphan_completed_report(_state_dict(), report_dir=str(rd)) is None


def test_mark_salvaged_completed_rewrites_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    pid = "pipe_wave9salv"
    pdir = tmp_path / pid
    (pdir / "handoff").mkdir(parents=True)
    state = _state_dict()
    state["pipeline_id"] = pid
    state["error"] = "后端在运行中被中断（进程重启），该管线已标记为失败。"
    state["stages"]["report"]["message"] = "多种子集成：并行 2 个额外种子（并发 2）…"
    (pdir / "pipeline_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert PipelineManager.mark_salvaged_completed(pid, "集成窗口被中断，已跳过") is True
    data = json.loads((pdir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["error"] is None
    assert data["global_progress"] == 100
    ph = data["options"]["pipeline_health"]
    assert ph["status"] == "degraded"
    assert "集成窗口被中断，已跳过" in ph["issues"]
    assert data["options"]["ensemble_done"] is True
    assert data["stages"]["report"]["status"] == "completed"
    assert data["stages"]["report"]["message"] == "报告完成（多种子集成被中断，已跳过）"


# ---------------------------------------------------------------- W9-5 语义对齐
_CRIT_BULL = "TSMC 2030 revenue >= $1.6T with foundry share >= 62% per Gartner"
_CRIT_BEAR = "semiconductor downcycle keeps 2030 market between $1.1T and $1.45T"


def test_semantic_alignment_merges_cross_seed_synonyms(monkeypatch):
    monkeypatch.setattr(Config, "ENSEMBLE_SEMANTIC_ALIGN", True, raising=False)
    monkeypatch.setattr(Config, "ENSEMBLE_EXTREMIZE_A", None, raising=False)
    runs = [
        _fc([("Structural Supercycle Holds", 0.45, _CRIT_BULL),
             ("Shallow Bear", 0.55, _CRIT_BEAR)]),
        _fc([("情景A：AI超级周期持续兑现（基线情景）", 0.44, _CRIT_BULL),
             ("情景B：浅熊回调", 0.56, _CRIT_BEAR)]),
        _fc([("S1 — AI长周期延续 (BULL/BASE)", 0.36, _CRIT_BULL),
             ("S2 — 下行情景", 0.64, _CRIT_BEAR)]),
    ]
    agg = EN.aggregate_forecasts(runs)
    # 6 个自由名 → 2 个语义桶，每桶 support=3（诊断实测的 11 孤桶问题不再出现）
    assert len(agg["scenarios"]) == 2
    assert all(s["support"] == 3 for s in agg["scenarios"])
    assert all(len(s.get("aliases") or []) == 3 for s in agg["scenarios"])
    # 有效共识桶 ≥2 → 一致度是数值而非 None/0 误报
    assert isinstance(agg["agreement"], float)
    assert agg["agreement"] > 0.5
    assert agg["semantic_alignment"] is True


def test_agreement_none_when_alignment_fails(monkeypatch):
    monkeypatch.setattr(Config, "ENSEMBLE_SEMANTIC_ALIGN", True, raising=False)
    runs = [
        _fc([("A", 1.0, "alpha beta gamma delta")]),
        _fc([("B", 1.0, "epsilon zeta eta theta")]),
    ]
    agg = EN.aggregate_forecasts(runs)
    assert all(s["support"] == 1 for s in agg["scenarios"])
    # 对齐后无一个 support≥2 的共识桶 → 一致度无法判定 → None（而非 0.0 误报）
    assert agg["agreement"] is None
    # None → medium 信心（不再被误降为 low）
    assert PipelineOrchestrator._agreement_to_confidence(agg["agreement"]) == "medium"


def test_same_run_scenarios_never_merge(monkeypatch):
    monkeypatch.setattr(Config, "ENSEMBLE_SEMANTIC_ALIGN", True, raising=False)
    crit = "identical resolution criteria tokens 2030 revenue threshold"
    buckets = EN.align_scenario_buckets([_fc([("Bull case", 0.5, crit),
                                              ("Bear case", 0.5, crit)])])
    assert len(buckets) == 2  # 同 run 情景按设计互斥，判据雷同也不互并


def test_alignment_flag_off_reverts_to_exact_name(monkeypatch):
    monkeypatch.setattr(Config, "ENSEMBLE_SEMANTIC_ALIGN", False, raising=False)
    crit = "same criteria tokens everywhere 2030 revenue"
    agg = EN.aggregate_forecasts([_fc([("Name One", 0.6, crit)]),
                                  _fc([("Name Two", 0.6, crit)])])
    assert len(agg["scenarios"]) == 2          # legacy：仅精确名匹配，不合并
    assert agg["agreement"] is not None        # legacy 一致度语义保持数值
    assert agg["semantic_alignment"] is False


# ---------------------------------------------------------------- W9-4 轨存活判定
def test_track_artifacts_survived(tmp_path):
    hd = str(tmp_path)
    assert _track_artifacts_survived(hd) is False                       # 无报告
    (tmp_path / "research_report.md").write_text("短", encoding="utf-8")
    assert _track_artifacts_survived(hd) is False                       # 报告过短
    (tmp_path / "research_report.md").write_text("研" * 500, encoding="utf-8")
    assert _track_artifacts_survived(hd) is False                       # 无结构化产物/断点
    (tmp_path / "research_checkpoint.json").write_text(
        json.dumps({"completed_passes": ["deep-opening", "deep-phase-1"]}), encoding="utf-8")
    assert _track_artifacts_survived(hd) is True                        # 报告 + 断点 → 存活
    (tmp_path / "research_checkpoint.json").unlink()
    assert _track_artifacts_survived(hd) is False
    (tmp_path / "actors.json").write_text("{}", encoding="utf-8")
    assert _track_artifacts_survived(hd) is True                        # 报告 + actors → 存活


# ---------------------------------------------------------------- W9-7 卷宗整理
def test_demote_track_headings():
    md = ("# Track 1: 基线证据扫描 (Base Evidence Sweep)\n\nbody one\n\n"
          "# Track 2: 基率·参照类·历史类比 (Base Rates & Reference Classes)\n\nbody two")
    out = demote_track_headings(md)
    assert "## Part 1 — Base Evidence Sweep" in out
    assert "## Part 2 — Base Rates & Reference Classes" in out
    assert "# Track" not in out
    assert "body one" in out and "body two" in out


def test_extract_exec_summary():
    md = "# Track\n\n## Executive Summary\n\nkey findings here\n\n## Next Section\n\nother"
    assert extract_exec_summary(md) == "key findings here"
    assert extract_exec_summary("plain text without headings").startswith("plain text")
    assert extract_exec_summary("x" * 9000, max_chars=100) == "x" * 100


def test_reconcile_track_reports_applies_opening_and_demotes():
    reports = [
        ("基线证据扫描 (Base Evidence Sweep)",
         "## Executive Summary\n\n" + "track one findings. " * 40),
        ("基率·参照类·历史类比 (Base Rates & Reference Classes)",
         "## Executive Summary\n\n" + "track two findings. " * 40),
    ]
    merged = merge_track_reports(reports)
    good = ("## Executive Summary\n\n" + "merged narrative. " * 30
            + "\n\n### Cross-track disagreements\n\n- 2030 TAM: 轨1 $1.5T vs 轨2 $1.8T")
    out, audit = reconcile_track_reports(merged, reports, llm=_StubLLM(good))
    assert audit["applied"] is True
    assert out.startswith("## Executive Summary")
    assert "### Cross-track disagreements" in out
    assert "## Part 1 — Base Evidence Sweep" in out
    assert "# Track 1:" not in out


def test_reconcile_track_reports_degrades_safely():
    reports = [("T1 (Alpha)", "## Executive Summary\n\n" + "one. " * 50),
               ("T2 (Beta)", "## Executive Summary\n\n" + "two. " * 50)]
    merged = merge_track_reports(reports)
    # LLM 抛异常 → 原文返回
    out, audit = reconcile_track_reports(merged, reports, llm=_StubLLM(RuntimeError("boom")))
    assert out == merged and audit["applied"] is False
    # 输出过短 → 拒绝
    out2, audit2 = reconcile_track_reports(merged, reports, llm=_StubLLM("short"))
    assert out2 == merged and audit2["applied"] is False
    # 单轨 → 无需整理
    out3, audit3 = reconcile_track_reports("# Track 1: X\n\nbody", [("X", "body")],
                                           llm=_StubLLM("anything long " * 50))
    assert out3 == "# Track 1: X\n\nbody" and audit3["reason"] == "tracks<2"


def test_reconcile_prompt_is_bounded():
    big = "## Executive Summary\n\n" + ("huge evidence block. " * 5000)
    reports = [("T1 (Alpha)", big), ("T2 (Beta)", big), ("T3 (Gamma)", big)]
    stub = _StubLLM("## Executive Summary\n\n" + "ok. " * 100)
    reconcile_track_reports(merge_track_reports(reports), reports, llm=stub,
                            max_prompt_chars=30000)
    prompt = stub.calls[0][0]["content"]
    assert len(prompt) < 32000  # 摘要 payload 被 cap 在 ~30K


# ---------------------------------------------------------------- W9-3 遥测增量落盘
def test_telemetry_incremental_flush_preserves_previous_attempt(tmp_path, monkeypatch):
    from app.utils.telemetry import LLMMeter
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    pid = "pipe_wave9tel"
    pdir = tmp_path / pid
    pdir.mkdir(parents=True)
    prev = {"total": {"calls": 5, "cached": 0, "prompt_tokens": 100, "completion_tokens": 50,
                      "total_tokens": 150, "latency_ms": 10.0, "cost_usd": 0.01},
            "report_id": "report_old", "status": "failed"}
    (pdir / "run_telemetry.json").write_text(json.dumps(prev), encoding="utf-8")

    LLMMeter.reset(pid)
    orch = PipelineOrchestrator()
    state = PipelineState(pipeline_id=pid, prompt="q")
    orch._init_telemetry_flush(state)
    LLMMeter.record("minimax", "m2", 10, 5, 100.0, run_id=pid, stage="report")
    orch._flush_run_telemetry(state)

    data = json.loads((pdir / "run_telemetry.json").read_text(encoding="utf-8"))
    assert data["in_flight"] is True
    assert data["total"]["calls"] == 1
    assert data["previous_attempt"]["total"]["calls"] == 5
    assert data["cumulative_total"]["calls"] == 6

    # 再记一笔并终版落盘：合并基底固定在 attempt 起点，绝不重复累计中间快照
    LLMMeter.record("minimax", "m2", 10, 5, 100.0, run_id=pid, stage="report")
    orch._flush_run_telemetry(state, final=True, extra={"note": "final"})
    data2 = json.loads((pdir / "run_telemetry.json").read_text(encoding="utf-8"))
    assert "in_flight" not in data2
    assert data2["total"]["calls"] == 2
    assert data2["cumulative_total"]["calls"] == 7
    assert data2["note"] == "final"
    LLMMeter.reset(pid)


def test_telemetry_maybe_flush_throttles_by_call_count(tmp_path, monkeypatch):
    from app.utils.telemetry import LLMMeter
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "PIPELINE_TELEMETRY_FLUSH_EVERY_CALLS", 3, raising=False)
    pid = "pipe_wave9tel2"
    (tmp_path / pid).mkdir(parents=True)
    LLMMeter.reset(pid)
    orch = PipelineOrchestrator()
    state = PipelineState(pipeline_id=pid, prompt="q")
    orch._init_telemetry_flush(state)
    tel_path = tmp_path / pid / "run_telemetry.json"

    LLMMeter.record("minimax", "m2", 1, 1, 1.0, run_id=pid)
    LLMMeter.record("minimax", "m2", 1, 1, 1.0, run_id=pid)
    orch._maybe_flush_run_telemetry(state)
    assert not tel_path.exists()          # 未达步长（2<3）→ 不写
    LLMMeter.record("minimax", "m2", 1, 1, 1.0, run_id=pid)
    orch._maybe_flush_run_telemetry(state)
    assert tel_path.exists()              # 达步长 → 落盘
    assert json.loads(tel_path.read_text())["total"]["calls"] == 3
    # 关闭增量（N<=0）→ no-op
    monkeypatch.setattr(Config, "PIPELINE_TELEMETRY_FLUSH_EVERY_CALLS", 0, raising=False)
    tel_path.unlink()
    LLMMeter.record("minimax", "m2", 1, 1, 1.0, run_id=pid)
    orch._maybe_flush_run_telemetry(state)
    assert not tel_path.exists()
    LLMMeter.reset(pid)


# ---------------------------------------------------------------- W9-1 打捞脚本
def _load_salvage_script():
    path = os.path.join(REPO_ROOT, "scripts", "salvage_orphaned_pipelines.py")
    spec = importlib.util.spec_from_file_location("salvage_orphaned_pipelines", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_synthetic_dirs(tmp_path):
    pipes = tmp_path / "pipelines"
    reports = tmp_path / "reports"
    # 可打捞：failed + report 阶段 completed + 完整报告
    pd = pipes / "pipe_salvageme"
    pd.mkdir(parents=True)
    rd = reports / "report_good"
    rd.mkdir(parents=True)
    (rd / "full_report.md").write_text("# 完整报告", encoding="utf-8")
    (rd / "forecast.json").write_text(
        json.dumps({"scenarios": [{"name": "A", "probability": 1.0}]}), encoding="utf-8")
    (pd / "pipeline_state.json").write_text(json.dumps({
        "pipeline_id": "pipe_salvageme", "status": "failed",
        "error": "后端在运行中被中断（进程重启），该管线已标记为失败。",
        "report_id": "report_good",
        "stages": {"report": {"name": "report", "status": "completed", "progress": 100,
                              "message": "多种子集成：并行 2 个额外种子（并发 2）…"}},
        "options": {},
    }, ensure_ascii=False), encoding="utf-8")
    # 不可打捞：failed 但报告目录缺 forecast.json
    pd2 = pipes / "pipe_reallybroken"
    pd2.mkdir(parents=True)
    rd2 = reports / "report_bad"
    rd2.mkdir(parents=True)
    (rd2 / "full_report.md").write_text("x", encoding="utf-8")
    (pd2 / "pipeline_state.json").write_text(json.dumps({
        "pipeline_id": "pipe_reallybroken", "status": "failed", "report_id": "report_bad",
        "stages": {"report": {"status": "completed"}}, "options": {},
    }), encoding="utf-8")
    # 不可打捞：completed（非终态失败，不在扫描范围）
    pd3 = pipes / "pipe_alreadyok"
    pd3.mkdir(parents=True)
    (pd3 / "pipeline_state.json").write_text(json.dumps({
        "pipeline_id": "pipe_alreadyok", "status": "completed", "report_id": "report_good",
        "stages": {"report": {"status": "completed"}}, "options": {},
    }), encoding="utf-8")
    return pipes, reports


def test_salvage_script_dry_run_is_readonly(tmp_path):
    mod = _load_salvage_script()
    pipes, reports = _make_synthetic_dirs(tmp_path)
    state_path = pipes / "pipe_salvageme" / "pipeline_state.json"
    before = state_path.read_text(encoding="utf-8")
    buf = io.StringIO()
    cands = mod.run(str(pipes), str(reports), apply=False, out=buf)
    assert [c["pipeline_id"] for c in cands] == ["pipe_salvageme"]
    assert cands[0]["report_id"] == "report_good"
    # dry-run 绝不改文件
    assert state_path.read_text(encoding="utf-8") == before
    assert (pipes / "pipe_reallybroken" / "pipeline_state.json").exists()
    assert "DRY-RUN" in buf.getvalue()


def test_salvage_script_mutation_matches_backend_semantics(tmp_path):
    """函数级验证 salvage_state 的改写字段（不经 --apply 运行脚本）。"""
    mod = _load_salvage_script()
    pipes, reports = _make_synthetic_dirs(tmp_path)
    data = json.loads(
        (pipes / "pipe_salvageme" / "pipeline_state.json").read_text(encoding="utf-8"))
    mutated = mod.salvage_state(data)
    assert mutated["status"] == "completed"
    assert mutated["error"] is None
    assert mutated["global_progress"] == 100
    assert mutated["options"]["pipeline_health"]["status"] == "degraded"
    assert mutated["options"]["ensemble_done"] is True
    assert mutated["stages"]["report"]["message"] == "报告完成（多种子集成被中断，已跳过）"
    assert mutated["current_stage"] == "report"


def test_salvage_script_rejects_path_escape_report_ids(tmp_path):
    mod = _load_salvage_script()
    reports = tmp_path / "reports"
    reports.mkdir()
    bad = {"report_id": "../../etc", "status": "failed",
           "stages": {"report": {"status": "completed"}}}
    assert mod.eligible_report(bad, str(reports)) is None


# ---------------------------------------------------------------- W9 旋钮默认值
def test_wave9_config_defaults():
    expected = {
        "ENSEMBLE_SEMANTIC_ALIGN": True,
        "ENSEMBLE_ALIGN_MIN_OVERLAP": 0.34,
        "PIPELINE_SALVAGE_COMPLETED_ORPHANS": True,
        "PIPELINE_TELEMETRY_FLUSH_EVERY_CALLS": 20,
        "RESEARCH_MCP_KG": True,
        "RESEARCH_TRACK_RECONCILE": True,
        "REPORT_LINT": True,
        "REPORT_TELEMETRY_APPENDIX": False,
        "GRAPH_CHUNK_SOURCE": "dossier_only",
    }
    for name, default in expected.items():
        assert hasattr(Config, name), f"config.py 缺定义: {name}"
        if os.environ.get(name):  # 本机 .env 显式覆盖的跳过取值断言
            continue
        assert getattr(Config, name) == default, f"{name} 默认漂移"
