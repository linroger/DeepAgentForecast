"""Offline unit tests for orchestrator research-stage wiring.

Covers the pure helpers landed for R2-RES-7 (as_of anchor validation) and
R2-RES-3 (advisory forecast-confidence penalty). No network / LLM / disk.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest

from app.services.pipeline_orchestrator import PipelineOrchestrator


# ── R2-RES-7: as_of anchor validation ───────────────────────────────────────

def _src(date_str):
    return {"title": "x", "url": "u", "tier": "S1", "date": date_str}


def test_as_of_valid_within_bounds_is_kept():
    actors = {"as_of_date": "2026-05-01"}
    sources = [_src("2026-01-01"), _src("2026-04-15")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is None
    assert dt is not None and dt.date().isoformat() == "2026-05-01"


def test_as_of_future_falls_back_to_max_source():
    future = (datetime.now(timezone.utc) + timedelta(days=400)).date().isoformat()
    actors = {"as_of_date": future}
    sources = [_src("2026-02-01"), _src("2026-03-20")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "晚于运行日" in note
    assert dt is not None and dt.date().isoformat() == "2026-03-20"


def test_as_of_predates_evidence_falls_back():
    actors = {"as_of_date": "2024-01-01"}
    sources = [_src("2026-02-01"), _src("2026-03-20")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "早于最新来源日" in note
    assert dt is not None and dt.date().isoformat() == "2026-03-20"


def test_as_of_unparseable_with_sources_falls_back():
    actors = {"as_of_date": "sometime last spring"}
    sources = [_src("2026-02-01")]
    dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
    assert note is not None and "无法解析" in note
    assert dt is not None and dt.date().isoformat() == "2026-02-01"


def test_as_of_absent_no_sources_preserves_none():
    # degrade-safe: no anchor available → byte-identical to today's None behavior.
    dt, note = PipelineOrchestrator._validate_as_of_date({}, None)
    assert dt is None and note is None
    dt2, note2 = PipelineOrchestrator._validate_as_of_date(None, [])
    assert dt2 is None and note2 is None


def test_as_of_future_no_sources_uses_run_date():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    dt, note = PipelineOrchestrator._validate_as_of_date({"as_of_date": future}, None)
    assert note is not None
    assert dt is not None and dt.date() == datetime.now(timezone.utc).date()


def test_as_of_garbage_inputs_never_raise():
    # tolerate any shape of dirty data
    for actors, sources in [(123, "nope"), ([], {}), ("x", [{"date": None}, 5])]:
        dt, note = PipelineOrchestrator._validate_as_of_date(actors, sources)
        assert dt is None or hasattr(dt, "date")


# ── R2-RES-3: advisory forecast-confidence penalty ──────────────────────────

def test_penalty_zero_when_no_signals():
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(None, None)
    assert pen == 0.0 and comp == {}


def test_penalty_from_low_research_quality():
    # Config.RESEARCH_QUALITY_FLOOR default 0.45; score 0.30 → 0.15 (capped).
    meta = {"research_quality": {"score": 0.30}}
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, None)
    assert comp.get("research_quality") == 0.15
    assert pen >= 0.15


def test_penalty_from_low_tier_sources():
    meta = {"source_tiers": {"S3": 4}}  # all low tier (weight 0.4) → (1-0.4)*0.1=0.06
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, None)
    assert comp.get("source_tier_mix") == 0.06
    # high-tier sources should yield no penalty
    pen2, comp2 = PipelineOrchestrator._compute_forecast_confidence_penalty(
        {"source_tiers": {"S1": 5}}, None)
    assert "source_tier_mix" not in comp2 and pen2 == 0.0


def test_penalty_from_weak_dossier_coverage():
    cov = {"n_actors": 5, "pct_actors_with_incentives": 0.1,
           "n_relationships": 3, "pct_edges_valenced": 0.0}
    pen, comp = PipelineOrchestrator._compute_forecast_confidence_penalty(None, cov)
    assert comp.get("dossier_coverage") == 0.1  # two weak signals × 0.05
    assert pen == 0.1


def test_penalty_capped_at_0_3():
    meta = {"research_quality": {"score": 0.0}, "source_tiers": {"S3": 10}}
    cov = {"n_actors": 5, "pct_actors_with_incentives": 0.0,
           "n_relationships": 0}
    pen, _ = PipelineOrchestrator._compute_forecast_confidence_penalty(meta, cov)
    assert pen <= 0.3


def test_penalty_includes_research_budget_exhaustion():
    pen, components = PipelineOrchestrator._compute_forecast_confidence_penalty(
        {"research_budget": {"denials": 3, "degraded": False}}, None)
    assert pen == 0.05
    assert components == {"research_budget": 0.05}


# ── ORCH-1(2): report-health placeholder detection (figure exemption) ───────

def _write_health_fixture(tmp_path, sections):
    """Write forecast.json + numbered section files; return the folder path."""
    import json as _json
    forecast_text = _json.dumps({"scenarios": [
        {
            "name": "A",
            "probability": 0.7,
            "resolution_criteria": "Outcome A is observed.",
        },
        {
            "name": "Other",
            "probability": 0.3,
            "resolution_criteria": "Any other outcome is observed.",
        },
    ]})
    (tmp_path / "forecast.json").write_text(forecast_text, encoding="utf-8")
    (tmp_path / "final_audit.json").write_text(
        _json.dumps({
            "policy_version": 3,
            "read_only": True,
            "markdown_sha256": "fixture",
            "forecast_sha256": hashlib.sha256(forecast_text.encode("utf-8")).hexdigest(),
            "disk_matches_memory": True,
            "hard_issues": [],
            "hard_passed": True,
            "structured_forecast": {"required": True, "present": True, "valid": True},
            "scenario_contract": {"valid": True, "issue_count": 0},
            "publish_gate": {
                "enabled": True,
                "passed": True,
                "hard_issues": [],
                "epistemic_issues": [],
                "hard_passed": True,
            },
        }),
        encoding="utf-8",
    )
    for i, body in enumerate(sections, start=1):
        (tmp_path / f"section_{i:02d}.md").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_report_health_short_figure_section_is_not_placeholder(monkeypatch, tmp_path):
    from app.services import pipeline_orchestrator as po
    folder = _write_health_fixture(tmp_path, [
        # <200 chars but carries figure markup → exempt from the placeholder rule
        "```mermaid\ngraph TD; A-->B;\n```\n图1：情景分支",
        "![对比图](data:image/png;base64,AAAA)",
        # <200 chars, plain prose, no figure → still a placeholder
        "太短。",
        # the exact report_agent failure template → placeholder
        "（本章节生成失败：LLM 返回空响应，请稍后重试）",
        # long prose that merely *mentions* 本章节/失败 → must NOT be a placeholder
        "本章节回顾了三次谈判失败的原因。" + "分析正文。" * 60,
    ])
    monkeypatch.setattr(po.ReportManager, "_get_report_folder", lambda rid: folder)
    health, issues, meta = po.PipelineOrchestrator._assess_report_health(None, "rid-1")
    assert meta["placeholder_sections"] == 2
    assert meta["sections"] == 5
    assert health == "degraded"  # partial placeholders → degraded, not failed
    assert any("2/5" in i for i in issues)


def test_report_health_surfaces_residual_simulation_mechanics(monkeypatch, tmp_path):
    from app.services import pipeline_orchestrator as po
    folder = _write_health_fixture(tmp_path, ["Analysis body. " * 40])
    forecast = json.loads((tmp_path / "forecast.json").read_text(encoding="utf-8"))
    forecast["quality"] = {
        "lint": {"leakage_flags": 2, "outcome_focus_ok": False}
    }
    forecast_text = json.dumps(forecast)
    (tmp_path / "forecast.json").write_text(forecast_text, encoding="utf-8")
    audit_path = tmp_path / "final_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["forecast_sha256"] = hashlib.sha256(
        forecast_text.encode("utf-8")
    ).hexdigest()
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    monkeypatch.setattr(po.ReportManager, "_get_report_folder", lambda rid: folder)

    health, issues, meta = po.PipelineOrchestrator._assess_report_health(None, "rid-1")

    assert health == "degraded"
    assert any("simulation-mechanics" in issue for issue in issues)
    assert any("simulation-mechanics" in issue for issue in meta["quality_issues"])


def test_report_health_hard_fails_final_publish_integrity_issue(monkeypatch, tmp_path):
    from app.services import pipeline_orchestrator as po

    folder = _write_health_fixture(tmp_path, ["Analysis body. " * 40])
    (tmp_path / "final_audit.json").write_text(json.dumps({
        "policy_version": 3,
        "read_only": True,
        "markdown_sha256": "dirty",
        "disk_matches_memory": True,
        "hard_issues": ["最终 Markdown 含 1 个悬空引用记号"],
        "hard_passed": False,
        "publish_gate": {
            "enabled": True,
            "passed": False,
            "hard_issues": ["最终 Markdown 含 1 个悬空引用记号"],
            "epistemic_issues": ["定量声明引用覆盖率 0.20 < 阈值 0.50"],
            "hard_passed": False,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(po.ReportManager, "_get_report_folder", lambda rid: folder)

    health, issues, meta = po.PipelineOrchestrator._assess_report_health(None, "rid-1")

    assert health == "failed"
    assert any("悬空引用" in issue for issue in issues)
    assert any("覆盖率" in issue for issue in issues)
    assert any("悬空引用" in issue for issue in meta["hard_quality_issues"])


def test_report_health_rejects_stale_final_audit_fingerprint(monkeypatch, tmp_path):
    from app.services import pipeline_orchestrator as po

    folder = _write_health_fixture(tmp_path, ["Analysis body. " * 40])
    report_md = "# Forecast\n\n" + ("Substantive outcome analysis. " * 100)
    (tmp_path / "full_report.md").write_text(report_md, encoding="utf-8")
    audit_path = tmp_path / "final_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["markdown_sha256"] = "0" * 64
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    monkeypatch.setattr(po.ReportManager, "_get_report_folder", lambda rid: folder)

    health, issues, _meta = po.PipelineOrchestrator._assess_report_health(None, "rid-1")

    assert health == "failed"
    assert any("fingerprint" in issue for issue in issues)


def test_report_health_hard_fails_when_final_audit_missing(monkeypatch, tmp_path):
    from app.services import pipeline_orchestrator as po

    folder = _write_health_fixture(tmp_path, ["Analysis body. " * 40])
    (tmp_path / "final_audit.json").unlink()
    monkeypatch.setattr(po.ReportManager, "_get_report_folder", lambda rid: folder)

    health, issues, _meta = po.PipelineOrchestrator._assess_report_health(None, "rid-1")

    assert health == "failed"
    assert any("not audited" in issue for issue in issues)


# ── VIZ-2: charts/datasets 可视化产物通道 specs & 临时产物发现 ──────────────────
# 覆盖 _viz_artifact_specs / _stage_artifact_specs / _discover_partial_artifacts 的
# 纯离线行为：索引锚点登记、开关门控、缺文件降级、原始 png|svg|csv 逐个透出。No disk-writes
# 到 uploads（仅 tmp_path handoff 目录）。

import json  # noqa: E402
import os  # noqa: E402

from app.services import pipeline_orchestrator as _po  # noqa: E402


def _viz_state(tmp_path):
    return _po.PipelineState(pipeline_id="viz-pipe-1", prompt="x", handoff_dir=str(tmp_path))


def test_viz_specs_present_for_research_and_report(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    st = _viz_state(tmp_path)
    specs = dict(_po.PipelineOrchestrator._stage_artifact_specs(st, _po.STAGE_RESEARCH))
    assert "charts" in specs and "datasets" in specs
    assert specs["charts"] == os.path.join(str(tmp_path), "charts.json")
    assert specs["datasets"].endswith(os.path.join("data", "datasets.json"))


def test_report_viz_specs_point_to_report_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    report_dir = tmp_path / "report"
    monkeypatch.setattr(_po.ReportManager, "_get_report_folder",
                        classmethod(lambda cls, rid: str(report_dir)))
    st = _viz_state(tmp_path / "handoff")
    st.report_id = "rid"
    specs = dict(_po.PipelineOrchestrator._stage_artifact_specs(st, _po.STAGE_REPORT))
    assert specs == {"report_viz_manifest": str(report_dir / "viz_manifest.json")}


def test_viz_specs_accept_legacy_nested_chart_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    legacy = tmp_path / "charts" / "charts.json"
    legacy.parent.mkdir()
    legacy.write_text("[]", encoding="utf-8")
    specs = dict(_po.PipelineOrchestrator._stage_artifact_specs(
        _viz_state(tmp_path), _po.STAGE_RESEARCH))
    assert specs["charts"] == str(legacy)


def test_dynamic_chart_specs_include_static_and_interactive_assets(tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    for name in ("actor.png", "timeline.svg", "actor.html", "ignore.json"):
        (charts / name).write_text("x", encoding="utf-8")
    specs = dict(_po.PipelineOrchestrator._viz_dynamic_artifact_specs(str(tmp_path)))
    assert set(specs) == {"chart_actor.png", "chart_timeline.svg", "chart_actor.html"}


def test_viz_specs_absent_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", False, raising=False)
    st = _viz_state(tmp_path)
    names = {n for n, _ in _po.PipelineOrchestrator._stage_artifact_specs(st, _po.STAGE_RESEARCH)}
    assert "charts" not in names and "datasets" not in names


def test_viz_absent_files_are_not_discovered(monkeypatch, tmp_path):
    # 旧跑：无 charts/ data/ 目录 → 索引锚点与原始文件都不出现，且不抛错（degrade-safe）。
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    st = _viz_state(tmp_path)
    partials = _po.PipelineOrchestrator._discover_partial_artifacts(st, _po.STAGE_RESEARCH)
    for p in partials:
        assert p["name"] not in ("charts", "datasets")
        assert not str(p["name"]).startswith(("chart_", "dataset_"))


def test_viz_present_files_surface_as_partials(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    (tmp_path / "charts").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "charts" / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "charts" / "fig1.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "charts" / "charts.json").write_text(
        '[{"title": "t", "caption": "c", "source_data": "data/fig1.csv"}]', encoding="utf-8")
    (tmp_path / "data" / "fig1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    st = _viz_state(tmp_path)
    # report_id 为空 → section_*.md 扫描分支自然跳过；不触碰 ReportManager。
    names = {p["name"] for p in _po.PipelineOrchestrator._discover_partial_artifacts(st, _po.STAGE_RESEARCH)}
    assert "charts" in names                # charts.json 经 specs 枚举登记为索引锚点
    assert "chart_fig1.png" in names        # 原始 png 经目录扫描逐个透出
    assert "chart_fig1.html" in names       # 交互式 HTML 同样可深链
    assert "dataset_fig1.csv" in names      # 原始 csv 经目录扫描逐个透出


def test_report_partial_scan_uses_report_owned_charts(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    handoff = tmp_path / "handoff"
    report_dir = tmp_path / "report"
    (report_dir / "charts").mkdir(parents=True)
    (report_dir / "charts" / "timeline.html").write_text("<html></html>", encoding="utf-8")
    (report_dir / "viz_manifest.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(_po.ReportManager, "_get_report_folder",
                        classmethod(lambda cls, rid: str(report_dir)))
    st = _viz_state(handoff)
    st.report_id = "rid"

    names = {p["name"] for p in _po.PipelineOrchestrator._discover_partial_artifacts(
        st, _po.STAGE_REPORT)}

    assert "report_viz_manifest" in names
    assert "report_chart_timeline.html" in names


def test_research_completion_registers_chart_assets_without_partial_suffix(
        monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    monkeypatch.setattr(_po.Config, "PIPELINE_VALIDATE_ARTIFACTS", False, raising=False)
    charts = tmp_path / "charts"
    charts.mkdir()
    (tmp_path / "charts.json").write_text("[]", encoding="utf-8")
    (charts / "actor.png").write_bytes(b"png")
    (charts / "actor.html").write_text("<html></html>", encoding="utf-8")
    st = _viz_state(tmp_path)
    st.artifacts["chart_actor.png_partial"] = str(charts / "actor.png")
    st.artifacts["chart_actor.html_partial"] = str(charts / "actor.html")
    orch = _po.PipelineOrchestrator.__new__(_po.PipelineOrchestrator)

    orch._record_stage_artifacts(st, _po.STAGE_RESEARCH)

    assert st.artifacts["chart_actor.png"] == str(charts / "actor.png")
    assert st.artifacts["chart_actor.html"] == str(charts / "actor.html")
    assert "chart_actor.png_partial" not in st.artifacts
    assert "chart_actor.html_partial" not in st.artifacts


def test_report_viz_reuse_validation_checks_every_declared_asset(tmp_path):
    report_dir = tmp_path / "report"
    charts = report_dir / "charts"
    charts.mkdir(parents=True)
    (charts / "timeline.html").write_text("<html></html>", encoding="utf-8")
    (charts / "timeline.png").write_bytes(b"png")
    (report_dir / "viz_manifest.json").write_text(json.dumps({
        "schema_version": 2,
        "items": [{
            "id": "timeline",
            "type": "html",
            "path": "charts/timeline.html",
            "png_path": "charts/timeline.png",
        }],
        "skipped": [],
    }), encoding="utf-8")

    assert _po.PipelineOrchestrator._validate_report_viz_references(str(report_dir)) is True
    (charts / "timeline.png").unlink()
    assert _po.PipelineOrchestrator._validate_report_viz_references(str(report_dir)) is False


def test_report_viz_reuse_validation_rejects_symlink(tmp_path):
    report_dir = tmp_path / "report"
    charts = report_dir / "charts"
    charts.mkdir(parents=True)
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")
    os.symlink(outside, charts / "timeline.html")
    (report_dir / "viz_manifest.json").write_text(json.dumps({
        "schema_version": 2,
        "items": [{"type": "html", "path": "charts/timeline.html"}],
    }), encoding="utf-8")

    assert _po.PipelineOrchestrator._validate_report_viz_references(str(report_dir)) is False


def test_new_report_attempt_clears_formal_partial_and_integrity_rows(monkeypatch, tmp_path):
    st = _viz_state(tmp_path)
    st.artifacts = {
        "report_viz_manifest": "/old/viz_manifest.json",
        "report_chart_timeline.html": "/old/charts/timeline.html",
        "section_01_partial": "/old/section_01.md",
        "report": "/handoff/research_report.md",
    }
    written = {}
    monkeypatch.setattr(
        _po.PipelineManager,
        "load_artifact_manifest",
        classmethod(lambda cls, pid: {
            "report_viz_manifest": {"path": "/old/viz_manifest.json"},
            "report_chart_timeline.html": {"path": "/old/charts/timeline.html"},
            "report": {"path": "/handoff/research_report.md"},
        }),
    )
    monkeypatch.setattr(
        _po.PipelineManager,
        "write_artifact_manifest",
        classmethod(lambda cls, pid, value: written.update(value)),
    )

    _po.PipelineOrchestrator._clear_report_attempt_artifacts(st)

    assert st.artifacts == {"report": "/handoff/research_report.md"}
    assert written == {"report": {"path": "/handoff/research_report.md"}}


def test_run_reuse_rejects_manifest_artifact_from_previous_simulation(
        monkeypatch, tmp_path):
    """A valid old run must not satisfy the manifest contract for a new PREPARE attempt."""
    run_root = tmp_path / "run-state"
    old_summary = run_root / "sim_old" / "run_summary.json"
    old_summary.parent.mkdir(parents=True)
    old_summary.write_text(json.dumps({
        "simulation_id": "sim_old",
        "rounds_executed": 19,
        "simulation_health": "ok",
    }), encoding="utf-8")
    entry = _po._manifest_entry_for("run_summary", str(old_summary), _po.STAGE_RUN)
    monkeypatch.setattr(_po.SimulationRunner, "RUN_STATE_DIR", str(run_root), raising=False)
    monkeypatch.setattr(
        _po.PipelineManager,
        "load_artifact_manifest",
        classmethod(lambda cls, pid: {"run_summary": entry}),
    )
    st = _po.PipelineState(
        pipeline_id="pipe_identity", prompt="q", mode="full", status="running")
    st.simulation_id = "sim_new"

    assert _po.PipelineOrchestrator()._validate_reuse(st, _po.STAGE_RUN) is False


def test_reuse_validation_exception_fails_closed(monkeypatch):
    orch = _po.PipelineOrchestrator()
    monkeypatch.setattr(
        orch,
        "_validate_reuse",
        lambda state, stage: (_ for _ in ()).throw(OSError("manifest read race")),
    )
    st = _po.PipelineState(
        pipeline_id="pipe_fail_closed", prompt="q", mode="full", status="running")

    assert orch._reuse_ok(st, _po.STAGE_RUN) is False


def test_prepare_reuse_accepts_the_recorded_persona_alternative(monkeypatch, tmp_path):
    """Duplicate persona candidates are one logical artifact, not two required files."""
    sim_root = tmp_path / "simulations"
    reddit_profiles = sim_root / "sim_one" / "reddit_profiles.json"
    reddit_profiles.parent.mkdir(parents=True)
    reddit_profiles.write_text('[{"name": "agent"}]', encoding="utf-8")
    entry = _po._manifest_entry_for("personas", str(reddit_profiles), _po.STAGE_PREPARE)
    monkeypatch.setattr(
        _po.Config, "OASIS_SIMULATION_DATA_DIR", str(sim_root), raising=False)
    monkeypatch.setattr(
        _po.PipelineManager,
        "load_artifact_manifest",
        classmethod(lambda cls, pid: {"personas": entry}),
    )
    st = _po.PipelineState(
        pipeline_id="pipe_persona", prompt="q", mode="full", status="running")
    st.simulation_id = "sim_one"

    assert _po.PipelineOrchestrator()._validate_reuse(st, _po.STAGE_PREPARE) is True


def test_run_reuse_requires_the_current_simulation_to_be_completed(monkeypatch, tmp_path):
    run_root = tmp_path / "run-state"
    summary = run_root / "sim_current" / "run_summary.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({
        "simulation_id": "sim_current",
        "rounds_executed": 19,
        "simulation_health": "ok",
    }), encoding="utf-8")
    monkeypatch.setattr(_po.SimulationRunner, "RUN_STATE_DIR", str(run_root), raising=False)
    st = _po.PipelineState(
        pipeline_id="pipe_status", prompt="q", mode="full", status="running")
    st.simulation_id = "sim_current"

    ready = SimpleNamespace(simulation_id="sim_current", status=_po.SimulationStatus.READY)
    completed = SimpleNamespace(
        simulation_id="sim_current", status=_po.SimulationStatus.COMPLETED)
    wrong = SimpleNamespace(simulation_id="sim_previous", status=_po.SimulationStatus.COMPLETED)

    assert _po.PipelineOrchestrator._run_reuse_ready(st, ready) is False
    assert _po.PipelineOrchestrator._run_reuse_ready(st, wrong) is False
    assert _po.PipelineOrchestrator._run_reuse_ready(st, completed) is True


def test_reused_run_summary_is_registered_without_rewriting(monkeypatch, tmp_path):
    run_root = tmp_path / "run-state"
    summary = run_root / "sim_current" / "run_summary.json"
    summary.parent.mkdir(parents=True)
    original = b'{"simulation_id":"sim_current","rounds_executed":19}\n'
    summary.write_bytes(original)
    monkeypatch.setattr(_po.SimulationRunner, "RUN_STATE_DIR", str(run_root), raising=False)
    writes = []
    monkeypatch.setattr(
        _po.SimulationRunner,
        "write_run_summary",
        classmethod(lambda cls, *args, **kwargs: writes.append((args, kwargs))),
    )
    monkeypatch.setattr(
        _po.PipelineManager, "save", classmethod(lambda cls, state: None))
    monkeypatch.setattr(
        _po.PipelineManager, "load_artifact_manifest", classmethod(lambda cls, pid: {}))
    recorded = {}
    monkeypatch.setattr(
        _po.PipelineManager,
        "write_artifact_manifest",
        classmethod(lambda cls, pid, value: recorded.update(value)),
    )
    st = _po.PipelineState(
        pipeline_id="pipe_preserve", prompt="q", mode="full", status="running")
    st.simulation_id = "sim_current"

    _po.PipelineOrchestrator()._publish_run_summary(
        st, "sim_current", communities=None, regenerate=False)

    assert writes == []
    assert summary.read_bytes() == original
    assert st.artifacts["run_summary"] == str(summary)
    assert recorded["run_summary"]["path"] == str(summary)


def test_missing_legacy_run_summary_is_backfilled_once(monkeypatch, tmp_path):
    run_root = tmp_path / "run-state"
    summary = run_root / "sim_legacy" / "run_summary.json"
    monkeypatch.setattr(_po.SimulationRunner, "RUN_STATE_DIR", str(run_root), raising=False)
    writes = []

    def write_summary(cls, simulation_id, communities=None):
        writes.append(simulation_id)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(json.dumps({
            "simulation_id": simulation_id,
            "rounds_executed": 7,
            "simulation_health": "ok",
        }), encoding="utf-8")

    monkeypatch.setattr(
        _po.SimulationRunner, "write_run_summary", classmethod(write_summary))
    monkeypatch.setattr(
        _po.PipelineManager, "save", classmethod(lambda cls, state: None))
    manifest = {}
    monkeypatch.setattr(
        _po.PipelineManager,
        "load_artifact_manifest",
        classmethod(lambda cls, pid: dict(manifest)),
    )
    monkeypatch.setattr(
        _po.PipelineManager,
        "write_artifact_manifest",
        classmethod(lambda cls, pid, value: manifest.update(value)),
    )
    st = _po.PipelineState(
        pipeline_id="pipe_legacy", prompt="q", mode="full", status="running")
    st.simulation_id = "sim_legacy"
    completed = SimpleNamespace(
        simulation_id="sim_legacy", status=_po.SimulationStatus.COMPLETED)

    assert _po.PipelineOrchestrator._run_reuse_ready(st, completed) is True
    orch = _po.PipelineOrchestrator()
    assert orch._publish_run_summary(
        st, "sim_legacy", communities=None, regenerate=False) is True
    assert orch._publish_run_summary(
        st, "sim_legacy", communities=None, regenerate=False) is True

    assert writes == ["sim_legacy"]
    assert st.artifacts["run_summary"] == str(summary)
    assert manifest["run_summary"]["path"] == str(summary)


def _exercise_prepare_run_resume(
        monkeypatch, tmp_path, *, rebuild_prepare, corrupt_run=False):
    """Run the real orchestrator state machine with every external service faked."""
    pipeline_root = tmp_path / "pipelines"
    simulation_root = tmp_path / "simulations"
    report_root = tmp_path / "reports" / "report_existing"
    report_root.mkdir(parents=True)
    monkeypatch.setattr(_po.Config, "PIPELINE_DATA_DIR", str(pipeline_root), raising=False)
    monkeypatch.setattr(
        _po.Config, "OASIS_SIMULATION_DATA_DIR", str(simulation_root), raising=False)
    monkeypatch.setattr(_po.SimulationRunner, "RUN_STATE_DIR", str(simulation_root), raising=False)
    for name, value in {
        "REPORT_LINT": False,
        "CAST_RECONCILE": False,
        "EMBED_WARM_AT_RESEARCH": False,
        "PIPELINE_VIZ_ARTIFACTS": False,
        "GRAPH_BUILD_COMMUNITIES": False,
        "GRAPH_RESOLVE_ENTITIES": False,
        "GRAPH_PRUNE_ENABLED": False,
        "SIM_GRAPH_FEEDBACK": False,
        "PIPELINE_RUN_STALL_S": 0,
        "REPORT_LLM_PREFLIGHT": False,
        "REPORT_TELEMETRY_APPENDIX": False,
        "SIM_TEMPORAL_MODE": "calendar",
        "SIM_DECISION_CHANNEL": True,
        "N_FORECAST_SEEDS": 1,
    }.items():
        monkeypatch.setattr(_po.Config, name, value, raising=False)

    pid = "pipe_state_machine"
    _po.PipelineManager.ensure_dirs(pid)
    handoff = _po.PipelineManager.handoff_dir(pid)
    os.makedirs(handoff, exist_ok=True)
    with open(os.path.join(handoff, "research_report.md"), "w", encoding="utf-8") as f:
        f.write("Evidence-backed EV research. " * 30)
    with open(os.path.join(handoff, "actors.json"), "w", encoding="utf-8") as f:
        json.dump({
            "as_of_date": "2026-07-12",
            "actors": [{"name": "EV OEM", "type": "Company"}],
            "relationships": [],
        }, f)
    with open(os.path.join(handoff, "sources.json"), "w", encoding="utf-8") as f:
        json.dump([], f)

    old_id = "sim_old"
    new_id = "sim_new"
    old_dir = simulation_root / old_id
    old_dir.mkdir(parents=True)
    old_config = old_dir / "simulation_config.json"
    old_config.write_text(json.dumps({
        "temporal_config": {
            "mode": "calendar",
            "unit": "year",
            "n_rounds": 9,
            "horizon_date": "2035-12-31",
            "horizon_source": "bare_year",
            "horizon_defaulted": False,
        },
        "world_state_seed": {
            "as_of_date": "2026-07-12",
            "horizon_date": "2035-12-31",
        },
    }, indent=2), encoding="utf-8")
    (old_dir / "twitter_profiles.csv").write_text("name\nEV OEM\n", encoding="utf-8")
    old_summary = old_dir / "run_summary.json"
    original_summary = json.dumps({
        "simulation_id": old_id,
        "rounds_executed": 9,
        "simulation_health": "ok",
    }, indent=2).encode()
    old_summary.write_bytes(original_summary)
    manifest = {
        "initial_posts": _po._manifest_entry_for(
            "initial_posts", str(old_config), _po.STAGE_PREPARE),
        "personas": _po._manifest_entry_for(
            "personas", str(old_dir / "twitter_profiles.csv"), _po.STAGE_PREPARE),
        "run_summary": _po._manifest_entry_for(
            "run_summary", str(old_summary), _po.STAGE_RUN),
    }
    _po.PipelineManager.write_artifact_manifest(pid, manifest)
    if rebuild_prepare:
        # Same path, different bytes: force the production manifest guard to
        # create a new PREPARE attempt while the old RUN bit remains completed.
        old_config.write_text(old_config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if corrupt_run:
        old_summary.write_bytes(original_summary + b"\n")

    states = {
        old_id: SimpleNamespace(
            simulation_id=old_id,
            project_id="proj",
            graph_id="graph",
            status=_po.SimulationStatus.COMPLETED,
        ),
    }
    manager_calls = {"create": 0, "prepare": 0}

    class FakeSimulationManager:
        def get_simulation(self, simulation_id):
            return states.get(simulation_id)

        def create_simulation(self, project_id, graph_id, **kwargs):
            manager_calls["create"] += 1
            states[new_id] = SimpleNamespace(
                simulation_id=new_id,
                project_id=project_id,
                graph_id=graph_id,
                status=_po.SimulationStatus.CREATED,
            )
            return states[new_id]

        def prepare_simulation(self, simulation_id, **kwargs):
            manager_calls["prepare"] += 1
            sim_dir = simulation_root / simulation_id
            sim_dir.mkdir(parents=True, exist_ok=True)
            (sim_dir / "simulation_config.json").write_text(json.dumps({
                "temporal_config": {
                    "mode": "calendar",
                    "unit": "year",
                    "n_rounds": 9,
                    "horizon_date": "2035-12-31",
                    "horizon_source": "bare_year",
                    "horizon_defaulted": False,
                },
            }, indent=2), encoding="utf-8")
            (sim_dir / "twitter_profiles.csv").write_text(
                "name\nEV OEM\n", encoding="utf-8")
            states[simulation_id].status = _po.SimulationStatus.READY
            return states[simulation_id]

        def _save_simulation_state(self, state):
            states[state.simulation_id] = state

    fake_manager = FakeSimulationManager()
    monkeypatch.setattr(_po, "SimulationManager", lambda: fake_manager)
    project = SimpleNamespace(
        project_id="proj",
        name="EV project",
        ontology={"entity_types": [{"name": "Company"}]},
        graph_id="graph",
    )
    monkeypatch.setattr(
        _po.ProjectManager, "get_project", classmethod(lambda cls, project_id: project))

    import app.services.zep_entity_reader as zep_reader

    class FakeEntityReader:
        def filter_defined_entities(self, graph_id, enrich_with_edges=False):
            return SimpleNamespace(entities=[{"uuid": "one"}])

    monkeypatch.setattr(zep_reader, "ZepEntityReader", FakeEntityReader)
    monkeypatch.setattr(
        _po,
        "GraphBuilderService",
        lambda **kwargs: SimpleNamespace(set_ontology=lambda graph_id, ontology: None),
    )

    start_calls = []
    summary_writes = []

    def start_simulation(cls, simulation_id, **kwargs):
        start_calls.append(simulation_id)
        states[simulation_id].status = _po.SimulationStatus.RUNNING

    def get_run_state(cls, simulation_id):
        return SimpleNamespace(
            total_rounds=9,
            current_round=9,
            runner_status=_po.RunnerStatus.COMPLETED,
        )

    def write_summary(cls, simulation_id, communities=None):
        summary_writes.append(simulation_id)
        path = simulation_root / simulation_id / "run_summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "simulation_id": simulation_id,
            "rounds_executed": 9,
            "simulation_health": "ok",
        }), encoding="utf-8")

    monkeypatch.setattr(
        _po.SimulationRunner, "start_simulation", classmethod(start_simulation))
    monkeypatch.setattr(
        _po.SimulationRunner, "get_run_state", classmethod(get_run_state))
    monkeypatch.setattr(
        _po.SimulationRunner, "write_run_summary", classmethod(write_summary))

    existing_report = SimpleNamespace(
        report_id="report_existing", status=_po.ReportStatus.COMPLETED)
    monkeypatch.setattr(
        _po.ReportManager,
        "get_report",
        classmethod(lambda cls, report_id: existing_report),
    )
    monkeypatch.setattr(
        _po.ReportManager,
        "get_report_by_simulation",
        classmethod(lambda cls, simulation_id: None),
    )
    monkeypatch.setattr(
        _po.ReportManager,
        "_get_report_folder",
        classmethod(lambda cls, report_id: str(report_root)),
    )

    # Keep this transition test focused on durable stage contracts, not provider,
    # telemetry, or final-report quality systems.
    monkeypatch.setattr(_po, "_finalize_research_contract", lambda *args, **kwargs: None)
    for name, replacement in {
        "_start_heartbeat": lambda self, state: None,
        "_init_telemetry_flush": lambda self, state: None,
        "_write_run_manifest": lambda self, state: None,
        "_update_manifest": lambda self, state, stage, **kwargs: None,
        "_record_research_telemetry": lambda self, state, value: None,
        "_maybe_warm_embedder": lambda self, state, actors: None,
        "_surface_research_quality": lambda self, state, handoff_dir: {},
        "_surface_forecast_confidence_penalty": lambda self, state, handoff_dir: None,
        "_flush_run_telemetry": lambda self, state, **kwargs: None,
        "_maybe_run_seed_ensemble": lambda self, *args, **kwargs: None,
        "_enforce_pipeline_health": lambda self, state: None,
        "_assess_report_health": lambda self, report_id: ("ok", [], {}),
    }.items():
        monkeypatch.setattr(_po.PipelineOrchestrator, name, replacement)

    state = _po.PipelineState(
        pipeline_id=pid,
        prompt="Forecast EV development through 2035",
        mode="full",
        status="running",
    )
    state.handoff_dir = handoff
    state.project_id = "proj"
    state.graph_id = "graph"
    state.simulation_id = old_id
    state.report_id = "report_existing"
    state.options["research_language"] = "English"
    if corrupt_run:
        state.options["scenario_overlay"] = {
            "injected_events": [{"content": "Policy shock", "round": 0}],
        }
    state.stages = {
        name: _po.StageState(name=name, status="completed", progress=100)
        for name in (
            _po.STAGE_RESEARCH,
            _po.STAGE_ONTOLOGY,
            _po.STAGE_GRAPH,
            _po.STAGE_PREPARE,
            _po.STAGE_RUN,
            _po.STAGE_REPORT,
        )
    }

    _po.PipelineOrchestrator._run(state)
    return SimpleNamespace(
        state=state,
        old_id=old_id,
        new_id=new_id,
        simulation_root=simulation_root,
        original_summary=original_summary,
        start_calls=start_calls,
        summary_writes=summary_writes,
        manager_calls=manager_calls,
        manifest=_po.PipelineManager.load_artifact_manifest(pid),
    )


def test_prepare_rebuild_invalidates_and_executes_run_end_to_end(monkeypatch, tmp_path):
    result = _exercise_prepare_run_resume(
        monkeypatch, tmp_path, rebuild_prepare=True)

    assert result.state.status == "completed"
    assert result.state.simulation_id == result.new_id
    assert result.manager_calls == {"create": 1, "prepare": 1}
    assert result.start_calls == [result.new_id]
    assert result.summary_writes == [result.new_id]
    config_path = result.simulation_root / result.new_id / "simulation_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["world_state_seed"]["horizon_date"] == "2035-12-31"
    entry = result.manifest["initial_posts"]
    assert os.path.realpath(entry["path"]) == os.path.realpath(config_path)
    assert entry["sha256"] == _po._sha256_file(str(config_path))
    assert result.manifest["run_summary"]["path"].endswith(
        f"{result.new_id}/run_summary.json")
    assert result.state.stages[_po.STAGE_RUN].message == "模拟完成"


def test_prepare_and_run_reuse_is_read_only_end_to_end(monkeypatch, tmp_path):
    result = _exercise_prepare_run_resume(
        monkeypatch, tmp_path, rebuild_prepare=False)

    assert result.state.status == "completed"
    assert result.state.simulation_id == result.old_id
    assert result.manager_calls == {"create": 0, "prepare": 0}
    assert result.start_calls == []
    assert result.summary_writes == []
    summary = result.simulation_root / result.old_id / "run_summary.json"
    assert summary.read_bytes() == result.original_summary
    assert result.state.stages[_po.STAGE_RUN].message == "模拟已恢复"


def test_invalid_run_manifest_applies_overlay_before_rerun(monkeypatch, tmp_path):
    result = _exercise_prepare_run_resume(
        monkeypatch, tmp_path, rebuild_prepare=False, corrupt_run=True)

    assert result.state.status == "completed"
    assert result.state.simulation_id == result.old_id
    assert result.manager_calls == {"create": 0, "prepare": 0}
    assert result.start_calls == [result.old_id]
    assert result.summary_writes == [result.old_id]
    config_path = result.simulation_root / result.old_id / "simulation_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["event_config"]["scheduled_events"][0]["content"] == "Policy shock"
    assert result.state.stages[_po.STAGE_RUN].message == "模拟完成"


def test_research_html_artifact_is_raw_served_in_opaque_sandbox(monkeypatch, tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    chart = charts / "actor.html"
    chart.write_text("<html><script>window.ok=1</script></html>", encoding="utf-8")
    monkeypatch.setattr(
        _po.PipelineManager,
        "handoff_dir",
        classmethod(lambda cls, pid: str(tmp_path)),
    )
    monkeypatch.setattr(
        _po.PipelineManager,
        "load",
        classmethod(lambda cls, pid: {
            "artifacts": {"chart_actor.html_partial": str(chart)},
        }),
    )
    from app import create_app
    client = create_app().test_client()

    resp = client.get("/api/research/pipe/artifact/chart_actor.html")

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert resp.data.startswith(b"<html>")
    assert "sandbox allow-scripts" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_research_svg_artifact_disables_scripts(monkeypatch, tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    chart = charts / "active.svg"
    chart.write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                     encoding="utf-8")
    monkeypatch.setattr(_po.PipelineManager, "handoff_dir",
                        classmethod(lambda cls, pid: str(tmp_path)))
    monkeypatch.setattr(_po.PipelineManager, "load", classmethod(lambda cls, pid: {
        "artifacts": {"chart_active.svg": str(chart)},
    }))
    from app import create_app
    resp = create_app().test_client().get("/api/research/pipe/artifact/chart_active.svg")

    assert resp.status_code == 200
    assert "sandbox;" in resp.headers["Content-Security-Policy"]
    assert "script-src 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_research_chart_rejects_post_registration_symlink_swap(monkeypatch, tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<script>window.stolen=1</script>", encoding="utf-8")
    swapped = charts / "actor.html"
    os.symlink(outside, swapped)
    monkeypatch.setattr(_po.PipelineManager, "handoff_dir",
                        classmethod(lambda cls, pid: str(tmp_path)))
    monkeypatch.setattr(_po.PipelineManager, "load", classmethod(lambda cls, pid: {
        "artifacts": {"chart_actor.html_partial": str(swapped)},
    }))
    from app import create_app
    resp = create_app().test_client().get("/api/research/pipe/artifact/chart_actor.html")

    assert resp.status_code == 404


# ── ITEM-18: 阶段级墙钟提取 ──────────────────────────────────────────────────

def test_stage_walls_computes_seconds_and_skips_incomplete():
    # 各阶段 started_at→finished_at 差值（秒）；缺一端/未结束的阶段跳过（degrade-safe）。
    st = _po.PipelineState(pipeline_id="pipe-walls-1", prompt="x")
    st.stages = {
        "research": _po.StageState(
            name="research", started_at="2026-05-01T00:00:00+00:00",
            finished_at="2026-05-01T00:00:42+00:00"),
        "graph": _po.StageState(
            name="graph", started_at="2026-05-01T00:01:00+00:00",
            finished_at="2026-05-01T00:01:05.500000+00:00"),
        "run": _po.StageState(name="run", started_at="2026-05-01T00:02:00+00:00"),  # 未结束
        "report": _po.StageState(name="report"),  # 未开始
    }
    walls = _po._stage_walls(st)
    assert walls == {"research": 42.0, "graph": 5.5}
    assert "run" not in walls and "report" not in walls


def test_stage_walls_empty_state_is_empty():
    st = _po.PipelineState(pipeline_id="pipe-walls-2", prompt="x")
    assert _po._stage_walls(st) == {}


def test_reused_stage_completion_preserves_original_wall_clock(monkeypatch):
    """A resume bookkeeping pass MUST not turn idle downtime into stage runtime."""
    state = _po.PipelineState(pipeline_id="pipe-walls-reuse", prompt="x")
    state.stages["research"] = _po.StageState(
        name="research",
        status="running",
        started_at="2026-05-01T00:00:00+00:00",
        finished_at="2026-05-01T00:00:42+00:00",
    )
    orchestrator = PipelineOrchestrator()
    monkeypatch.setattr(orchestrator, "_record_stage_artifacts", lambda *_args: None)
    monkeypatch.setattr(orchestrator, "_flush_run_telemetry", lambda *_args: None)
    monkeypatch.setattr(_po.PipelineManager, "save", classmethod(lambda cls, _state: None))
    monkeypatch.setattr(_po, "_utcnow", lambda: "2026-05-01T08:00:00+00:00")

    orchestrator._complete_stage(
        state, "research", "研究报告已恢复", reused=True,
    )

    assert state.stages["research"].finished_at == "2026-05-01T00:00:42+00:00"
    assert _po._stage_walls(state)["research"] == 42.0


@pytest.mark.parametrize(
    ("started_at", "finished_at", "expected"),
    [
        ("2026-05-01T00:00:00+00:00", None, "2026-05-01T00:00:00+00:00"),
        (None, "2026-05-01T00:00:42+00:00", "2026-05-01T00:00:42+00:00"),
    ],
)
def test_reused_stage_completion_collapses_one_sided_legacy_timing(
    monkeypatch, started_at, finished_at, expected,
):
    """A missing legacy endpoint MUST not turn resume idle time into runtime."""
    state = _po.PipelineState(pipeline_id="pipe-walls-one-sided", prompt="x")
    state.stages["research"] = _po.StageState(
        name="research",
        status="running",
        started_at=started_at,
        finished_at=finished_at,
    )
    orchestrator = PipelineOrchestrator()
    monkeypatch.setattr(orchestrator, "_record_stage_artifacts", lambda *_args: None)
    monkeypatch.setattr(orchestrator, "_flush_run_telemetry", lambda *_args: None)
    monkeypatch.setattr(_po.PipelineManager, "save", classmethod(lambda cls, _state: None))
    monkeypatch.setattr(_po, "_utcnow", lambda: "2026-05-01T08:00:00+00:00")

    orchestrator._complete_stage(state, "research", "研究报告已恢复", reused=True)

    stage = state.stages["research"]
    assert stage.started_at == expected
    assert stage.finished_at == expected
    assert _po._stage_walls(state)["research"] == 0.0


def test_retry_stage_reset_starts_a_fresh_timing_window():
    stage = _po.StageState(
        name="report",
        status="failed",
        progress=96,
        message="old failed attempt",
        error="quality gate failed",
        started_at="2026-05-01T00:00:00+00:00",
        finished_at="2026-05-01T00:30:00+00:00",
    )

    _po._reset_stage_attempt(stage)

    assert stage.status == "pending"
    assert stage.progress == 0
    assert stage.message == ""
    assert stage.error is None
    assert stage.started_at is None
    assert stage.finished_at is None


def test_reconcile_completed_simulation_state_copies_authoritative_run_progress():
    simulation = SimpleNamespace(
        status=_po.SimulationStatus.RUNNING,
        enable_twitter=True,
        enable_reddit=True,
        current_round=0,
        twitter_status="not_started",
        reddit_status="not_started",
        error="stale",
    )
    run_state = SimpleNamespace(
        current_round=19,
        twitter_enabled=True,
        reddit_enabled=True,
        twitter_completed=True,
        reddit_completed=True,
        error=None,
    )

    PipelineOrchestrator._reconcile_completed_simulation_state(simulation, run_state)

    assert simulation.status == _po.SimulationStatus.COMPLETED
    assert simulation.current_round == 19
    assert simulation.twitter_status == "completed"
    assert simulation.reddit_status == "completed"
    assert simulation.error is None


def test_reconcile_completed_simulation_state_keeps_legacy_platforms_unknown():
    simulation = SimpleNamespace(
        status=_po.SimulationStatus.COMPLETED,
        enable_twitter=True,
        enable_reddit=True,
        current_round=0,
        twitter_status="not_started",
        reddit_status="not_started",
        error=None,
    )

    PipelineOrchestrator._reconcile_completed_simulation_state(
        simulation,
        run_state=None,
        run_summary={"rounds_executed": 19},
    )

    assert simulation.current_round == 19
    assert simulation.twitter_status == "unknown"
    assert simulation.reddit_status == "unknown"


def test_reconcile_loaded_legacy_run_state_uses_simulation_enable_flags(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import SimulationRunner

    simulation_id = "sim_legacy_enabled_flags"
    run_dir = tmp_path / simulation_id
    run_dir.mkdir()
    (run_dir / "run_state.json").write_text(
        json.dumps({
            "simulation_id": simulation_id,
            "runner_status": "completed",
            "current_round": 19,
            "twitter_completed": True,
            "reddit_completed": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    loaded = SimulationRunner._load_run_state(simulation_id)
    assert loaded is not None
    assert loaded.twitter_enabled is None
    assert loaded.reddit_enabled is None

    simulation = SimpleNamespace(
        status=_po.SimulationStatus.RUNNING,
        enable_twitter=True,
        enable_reddit=False,
        current_round=0,
        twitter_status="not_started",
        reddit_status="not_started",
        error=None,
    )
    PipelineOrchestrator._reconcile_completed_simulation_state(
        simulation,
        run_state=loaded,
        run_summary={"rounds_executed": 19},
    )

    assert simulation.twitter_status == "completed"
    assert simulation.reddit_status == "disabled"


def test_report_agent_accepts_charts_manifest_kwarg():
    # 附加 kwarg：仅存储、默认 None、不改变旧构造行为。
    from app.services.report_agent import ReportAgent
    manifest = [{"title": "t", "caption": "c", "source_data": "d"}]
    agent = ReportAgent.__new__(ReportAgent)  # 免全量构造（无需 LLM/图谱）
    # 直接断言签名接受该 kwarg 且默认 None 语义：用 __init__ 参数内省。
    import inspect
    sig = inspect.signature(ReportAgent.__init__)
    assert "charts_manifest" in sig.parameters
    assert sig.parameters["charts_manifest"].default is None
    del agent, manifest
