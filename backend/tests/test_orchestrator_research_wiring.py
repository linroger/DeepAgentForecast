"""Offline unit tests for orchestrator research-stage wiring.

Covers the pure helpers landed for R2-RES-7 (as_of anchor validation) and
R2-RES-3 (advisory forecast-confidence penalty). No network / LLM / disk.
"""

from datetime import datetime, timedelta, timezone

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


# ── ORCH-1(2): report-health placeholder detection (figure exemption) ───────

def _write_health_fixture(tmp_path, sections):
    """Write forecast.json + numbered section files; return the folder path."""
    import json as _json
    (tmp_path / "forecast.json").write_text(
        _json.dumps({"scenarios": [{"name": "A", "probability": 1.0}]}),
        encoding="utf-8")
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


# ── VIZ-2: charts/datasets 可视化产物通道 specs & 临时产物发现 ──────────────────
# 覆盖 _viz_artifact_specs / _stage_artifact_specs / _discover_partial_artifacts 的
# 纯离线行为：索引锚点登记、开关门控、缺文件降级、原始 png|svg|csv 逐个透出。No disk-writes
# 到 uploads（仅 tmp_path handoff 目录）。

import os  # noqa: E402

from app.services import pipeline_orchestrator as _po  # noqa: E402


def _viz_state(tmp_path):
    return _po.PipelineState(pipeline_id="viz-pipe-1", prompt="x", handoff_dir=str(tmp_path))


def test_viz_specs_present_for_research_and_report(monkeypatch, tmp_path):
    monkeypatch.setattr(_po.Config, "PIPELINE_VIZ_ARTIFACTS", True, raising=False)
    st = _viz_state(tmp_path)
    for stage in (_po.STAGE_RESEARCH, _po.STAGE_REPORT):
        specs = dict(_po.PipelineOrchestrator._stage_artifact_specs(st, stage))
        assert "charts" in specs and "datasets" in specs
        assert specs["charts"].endswith(os.path.join("charts", "charts.json"))
        assert specs["datasets"].endswith(os.path.join("data", "datasets.json"))


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
    (tmp_path / "charts" / "charts.json").write_text(
        '[{"title": "t", "caption": "c", "source_data": "data/fig1.csv"}]', encoding="utf-8")
    (tmp_path / "data" / "fig1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    st = _viz_state(tmp_path)
    # report_id 为空 → section_*.md 扫描分支自然跳过；不触碰 ReportManager。
    names = {p["name"] for p in _po.PipelineOrchestrator._discover_partial_artifacts(st, _po.STAGE_REPORT)}
    assert "charts" in names                # charts.json 经 specs 枚举登记为索引锚点
    assert "chart_fig1.png" in names        # 原始 png 经目录扫描逐个透出
    assert "dataset_fig1.csv" in names      # 原始 csv 经目录扫描逐个透出


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
