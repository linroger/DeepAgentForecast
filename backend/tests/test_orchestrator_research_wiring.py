"""Offline unit tests for orchestrator research-stage wiring.

Covers the pure helpers landed for R2-RES-7 (as_of anchor validation) and
R2-RES-3 (advisory forecast-confidence penalty). No network / LLM / disk.
"""

from datetime import datetime, timedelta, timezone
import hashlib

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
