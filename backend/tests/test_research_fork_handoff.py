"""Fork 情景共享 handoff 目录的 API 解析回归（LOOP-017 缺陷 3）。

fork() 让情景管线复用 base 的 handoff（state.handoff_dir 指向 base 目录），但 API 层
此前一律按 uploads/pipelines/<fork_id>/handoff 静态重算——该目录又被 ensure_dirs 建成
空壳，dossier/翻译/PDF/编辑/进度/图表端点对 fork 全部静默拿空（isdir 通过、内容为空，
比干净的 404 更糟）。本套件红先固定 PipelineManager.resolve_handoff_dir 的解析次序与
收容（state.handoff_dir 必须真实位于 PIPELINE_DATA_DIR 之下，否则回落静态路径），以及
dossier/progress/chart 端点对 fork 的实际行为。
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from app.api import research_bp
from app.config import Config
import app.services.pipeline_orchestrator as po
from app.services.pipeline_orchestrator import PipelineManager

BASE_ID = "pipe_basea1b2c3d4"
FORK_ID = "pipe_forkd4e5f6a7"


def _write_state(root, pid, **extra):
    d = root / pid
    (d / "handoff").mkdir(parents=True, exist_ok=True)
    state = {
        "pipeline_id": pid,
        "status": "completed",
        "schema_version": po.PIPELINE_SCHEMA_VERSION,
        "artifacts": {},
        "stages": {},
    }
    state.update(extra)
    (d / "pipeline_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix="/api/research")
    return app.test_client()


# ------------------------------------------------------------- resolver 单元
def test_resolver_prefers_state_handoff_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    _write_state(tmp_path, FORK_ID, handoff_dir=str(base / "handoff"),
                 options={"base_pipeline_id": BASE_ID})
    assert PipelineManager.resolve_handoff_dir(FORK_ID) == str(base / "handoff")


def test_resolver_static_for_non_fork(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, BASE_ID)
    assert (PipelineManager.resolve_handoff_dir(BASE_ID)
            == PipelineManager.handoff_dir(BASE_ID))


def test_resolver_rejects_escaping_state_path(tmp_path, monkeypatch):
    """状态文件里的 handoff_dir 若逃逸出 PIPELINE_DATA_DIR → 回落静态路径（防篡改）。"""
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, FORK_ID, handoff_dir="/etc")
    assert (PipelineManager.resolve_handoff_dir(FORK_ID)
            == PipelineManager.handoff_dir(FORK_ID))


def test_resolver_missing_state_falls_back_static(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    assert (PipelineManager.resolve_handoff_dir("pipe_nostate0001")
            == PipelineManager.handoff_dir("pipe_nostate0001"))


def test_resolver_invalid_id_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    with pytest.raises(ValueError):
        PipelineManager.resolve_handoff_dir("../escape")


# --------------------------------------------------------- 端点：fork 视角
def test_fork_dossier_served_from_base_handoff(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    (base / "handoff" / "research_report.md").write_text(
        "# 深度研究报告\n共享产物。", encoding="utf-8")
    (base / "handoff" / "actors.json").write_text(
        json.dumps({"actors": [{"name": "A"}]}), encoding="utf-8")
    _write_state(tmp_path, FORK_ID, handoff_dir=str(base / "handoff"))

    resp = client.get(f"/api/research/{FORK_ID}/dossier")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["has_report"] is True
    assert "共享产物" in data["report"]
    assert data["actors"] == {"actors": [{"name": "A"}]}


def test_fork_progress_served_from_base_handoff(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    (base / "handoff" / "research_progress.log").write_text(
        "2026-01-01T00:00:00+00:00 [init] client ready\n", encoding="utf-8")
    _write_state(tmp_path, FORK_ID, handoff_dir=str(base / "handoff"),
                 status="running")

    resp = client.get(f"/api/research/{FORK_ID}/progress?lines=10")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["returned"] == 1


def test_fork_chart_artifact_falls_back_to_shared_charts_dir(tmp_path, monkeypatch, client):
    """fork 的 artifacts 表生而为空（fork() 不复制 base 注册表）；chart_* 名称须能按
    解析后的共享 handoff/charts 目录直出，且沿用既有的 basename/symlink/收容复验。"""
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    charts = base / "handoff" / "charts"
    charts.mkdir(parents=True)
    (charts / "trend.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    _write_state(tmp_path, FORK_ID, handoff_dir=str(base / "handoff"))

    resp = client.get(f"/api/research/{FORK_ID}/artifact/chart_trend.png")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"

    # 收容复验仍然生效：不存在的文件名 → 404，不异常。
    resp2 = client.get(f"/api/research/{FORK_ID}/artifact/chart_missing.png")
    assert resp2.status_code == 404


def test_fork_chart_fallback_rejects_symlink_escape(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    charts = base / "handoff" / "charts"
    charts.mkdir(parents=True)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (charts / "evil.png").symlink_to(outside)
    _write_state(tmp_path, FORK_ID, handoff_dir=str(base / "handoff"))

    resp = client.get(f"/api/research/{FORK_ID}/artifact/chart_evil.png")
    assert resp.status_code == 404


def test_non_fork_chart_artifact_still_served_via_registry(tmp_path, monkeypatch, client):
    """非 fork 管线的既有注册表路径不受回退逻辑影响。"""
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    base = _write_state(tmp_path, BASE_ID)
    charts = base / "handoff" / "charts"
    charts.mkdir(parents=True)
    (charts / "trend.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    _write_state(tmp_path, BASE_ID,
                 artifacts={"chart_trend.png": str(charts / "trend.png")})

    resp = client.get(f"/api/research/{BASE_ID}/artifact/chart_trend.png")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
