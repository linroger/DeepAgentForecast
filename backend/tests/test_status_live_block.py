"""OBS-1/I-5-6 落地回归：/api/research/status/<id> 的计算型 ``live`` 块。

heartbeat_status/estimate_eta/LLMMeter.status_snapshot 三个助手此前没有任何生产调用方
（LOOP-017 缺陷 2）：UI 无从显示「管线还活着吗 / 还要多久 / 烧了多少 / 预算余量」。
本套件固定 status 端点的附加语义：live 是纯附加键（state 原文键不动）、终态 eta=0、
预算开启且进程内确有计量数据时给出 budget 余量，任何助手失败只丢 live 不 500。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from app.api import research_bp
from app.config import Config
import app.services.pipeline_orchestrator as po


PID = "pipe_livestatus01"


def _utc(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _write_state(root, pid, **extra):
    d = root / pid
    (d / "handoff").mkdir(parents=True, exist_ok=True)
    state = {
        "pipeline_id": pid,
        "status": "running",
        "schema_version": po.PIPELINE_SCHEMA_VERSION,
        "artifacts": {},
        "stages": {},
        "created_at": _utc(600),
        "updated_at": _utc(5),
        "heartbeat_at": _utc(10),
        "global_progress": 50,
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


def test_status_running_carries_live_block(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, PID)

    resp = client.get(f"/api/research/status/{PID}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    # 原文键不动。
    assert data["pipeline_id"] == PID and data["status"] == "running"
    live = data["live"]
    assert live["heartbeat_age_s"] is not None and 8 <= live["heartbeat_age_s"] <= 60
    assert live["elapsed_s"] is not None and live["elapsed_s"] >= 590
    # progress=50、elapsed≈600s → 线性外推 ETA≈600s（受 cap 约束但远未触顶）。
    assert live["eta_s"] is not None and 0 < live["eta_s"] <= 7200
    assert live["stale"] is False
    assert "owner_alive" in live


def test_status_terminal_eta_zero(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, PID, status="completed", global_progress=100)

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["eta_s"] == 0
    assert live["stale"] is False


def test_status_budget_block_with_metered_spend(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1_000_000, raising=False)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True, raising=False)
    _write_state(tmp_path, PID)

    from app.utils import telemetry as tel
    tel.set_run_context(PID, stage="graph")
    try:
        tel.LLMMeter.record("test-provider", "test-model", 1200, 300, 5.0)
    finally:
        tel.set_run_context(None)

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["tokens"] == 1500
    assert live["budget"]["limit_tokens"] == 1_000_000
    assert live["budget"]["spent_tokens"] == 1500
    assert live["budget"]["remaining_tokens"] == 1_000_000 - 1500


def test_status_survives_helper_failure(tmp_path, monkeypatch, client):
    """live 助手抛异常 → 只丢 live 键，状态端点仍 200 返回原文。"""
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, PID)
    monkeypatch.setattr(
        po.PipelineOrchestrator, "heartbeat_status",
        lambda self, state: (_ for _ in ()).throw(RuntimeError("boom")))

    resp = client.get(f"/api/research/status/{PID}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["pipeline_id"] == PID
    assert "live" not in data
