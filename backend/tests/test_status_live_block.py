"""OBS-1/I-5-6 落地回归：/api/research/status/<id> 的计算型 ``live`` 块。

heartbeat_status/estimate_eta/LLMMeter.status_snapshot 三个助手此前没有任何生产调用方
（LOOP-017 缺陷 2）：UI 无从显示「管线还活着吗 / 还要多久 / 烧了多少 / 预算余量」。
本套件固定 status 端点的附加语义：live 是纯附加键（state 原文键不动）、终态 eta=0、
预算开启且进程内确有计量数据时给出 budget 余量，任何助手失败只丢 live 不 500。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_status_terminal_eta_zero_and_frozen_elapsed(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    _write_state(tmp_path, PID, status="completed", global_progress=100)

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["eta_s"] == 0
    assert live["stale"] is False
    # i8（F2 取证）：终态 elapsed 冻结在 created/resumed→updated 的真实时长
    # （fixture: created 600s 前、updated 5s 前 → ≈595s），不再随「现在」增长。
    assert live["elapsed_s"] is not None and 585 <= live["elapsed_s"] <= 600


def test_estimate_eta_terminal_elapsed_omitted_on_missing_updated_at():
    """助手层守卫：终态且 updated_at 缺失 → elapsed 省略，绝不给误导数字。

    （API 路径到不了这里：from_dict 会把 null updated_at 回填成「现在」——守卫保护的
    是直接构造 PipelineState 的调用方。）"""
    state = po.PipelineState(pipeline_id="pipe_x", prompt="q", mode="full",
                             status="completed")
    state.created_at = _utc(600)
    state.updated_at = None
    out = po.PipelineOrchestrator().estimate_eta(state)
    assert out["elapsed_s"] is None
    assert out["eta_s"] == 0


def test_status_budget_block_with_metered_spend(tmp_path, monkeypatch, client):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1_000_000, raising=False)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True, raising=False)
    _write_state(tmp_path, PID)

    # A different worker commits two attempts; the serving process never
    # attaches an LLMMeter binding and must still see the cumulative total.
    subprocess.run([sys.executable, "-c", """
import sys
from app.utils.usage_ledger import UsageLedger
ledger = UsageLedger(sys.argv[1])
ledger.initialize(sys.argv[2])
for attempt in ('first', 'resumed'):
    ledger.record_snapshot(run_id=sys.argv[2], attempt_id=attempt, source='test',
        operation_id=attempt, metadata={'stage': 'graph', 'provider': 'test',
        'model': 'test', 'usage_class': 'known', 'billing_basis': 'estimated_api'},
        counters={'calls': 1, 'prompt_tokens': 600, 'completion_tokens': 150,
        'cost_usd': 0.02})
""", str(tmp_path / "usage_ledger.sqlite3"), PID], check=True,
                   cwd=Path(__file__).resolve().parents[1])
    from app.utils.telemetry import LLMMeter
    LLMMeter.reset(PID)

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["tokens"] == 1500
    assert live["budget"]["limit_tokens"] == 1_000_000
    assert live["budget"]["spent_tokens"] == 1500
    assert live["budget"]["remaining_tokens"] == 1_000_000 - 1500
    assert live["spend_so_far"]["source"] == "durable_ledger"
    assert live["spend_so_far"]["available"] is True
    assert live["spend_so_far"]["coverage"] == "recorded_observations"
    assert live["spend_so_far"]["usage_complete"] is False
    assert live["budget"]["usage_complete"] is False


@pytest.mark.parametrize("telemetry_enabled", [True, False])
def test_status_preserves_registered_zero_and_uses_actual_pipeline_root(
        tmp_path, monkeypatch, client, telemetry_enabled):
    from app.utils.usage_ledger import UsageLedger
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "unused"))
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", telemetry_enabled)
    monkeypatch.setattr(po.PipelineManager, "_dir", classmethod(lambda cls, pid: str(tmp_path / pid)))
    _write_state(tmp_path, PID)
    UsageLedger(str(tmp_path / "usage_ledger.sqlite3")).initialize(PID)

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["tokens"] == 0
    assert live["spend_so_far"]["cost_usd"] == 0
    assert live["spend_so_far"]["available"] is True
    assert live["budget"]["spent_tokens"] == 0
    assert live["budget"]["remaining_tokens"] == 1000
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("failure", ["absent", "unregistered", "corrupt", "lost", "durable_cache", "bad_cache", "wrong_run"])
def test_status_unknown_spend_never_becomes_unused_budget(tmp_path, monkeypatch, client, failure):
    from app.utils.usage_ledger import UsageLedger
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    run_dir = _write_state(tmp_path, PID, options={"usage_attempt_id": "old"} if failure == "lost" else {})
    ledger_path = tmp_path / "usage_ledger.sqlite3"
    if failure == "unregistered":
        UsageLedger(str(ledger_path)).initialize("another-run")
    if failure == "corrupt":
        ledger_path.write_bytes(b"not a sqlite database")
        # A valid stale cache must not conceal ledger corruption.
        (run_dir / "run_telemetry.json").write_text(json.dumps({
            "pipeline_id": PID, "total": {"total_tokens": 10, "cost_usd": 0.1}}))
    if failure in {"durable_cache", "wrong_run", "lost"}:
        (run_dir / "run_telemetry.json").write_text(json.dumps({
            "pipeline_id": "another-run" if failure == "wrong_run" else PID,
            "durable": failure == "durable_cache",
            "total": {"total_tokens": 0, "cost_usd": 0}}))
    if failure == "bad_cache":
        (run_dir / "run_telemetry.json").write_text("{broken")
    before = ledger_path.read_bytes() if ledger_path.exists() else None

    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["available"] is False
    assert live["spend_so_far"]["tokens"] is None
    assert live["spend_so_far"]["cost_usd"] is None
    assert live["spend_so_far"]["usage_complete"] is False
    assert live["budget"]["spent_tokens"] is None
    assert live["budget"]["remaining_tokens"] is None
    assert live["budget"]["available"] is False
    assert (ledger_path.read_bytes() if ledger_path.exists() else None) == before


@pytest.mark.parametrize("tokens", [0, 1700])
def test_status_legacy_saved_cumulative_snapshot_is_explicitly_partial(tmp_path, monkeypatch, client, tokens):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    run_dir = _write_state(tmp_path, PID)
    (run_dir / "run_telemetry.json").write_text(json.dumps({
        "pipeline_id": PID, "total": {"total_tokens": min(tokens, 10), "cost_usd": 0},
        "cumulative_total": {"total_tokens": tokens, "cost_usd": 0},
    }))
    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["available"] is True
    assert live["spend_so_far"]["tokens"] == tokens
    assert live["spend_so_far"]["source"] == "legacy_snapshot"
    assert live["spend_so_far"]["coverage"] == "legacy_saved_snapshot"
    assert live["spend_so_far"]["usage_complete"] is False
    assert not (tmp_path / "usage_ledger.sqlite3").exists()


@pytest.mark.parametrize("total", [{}, {"total_tokens": False}, {"total_tokens": -1},
                                  {"total_tokens": 1.5}, {"total_tokens": "0"},
                                  {"total_tokens": float("nan")}, {"cost_usd": float("inf")}])
def test_status_invalid_legacy_cumulative_total_cannot_fall_back_to_attempt_zero(
        tmp_path, monkeypatch, client, total):
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 1000)
    run_dir = _write_state(tmp_path, PID)
    (run_dir / "run_telemetry.json").write_text(json.dumps({
        "pipeline_id": PID, "total": {"total_tokens": 0, "cost_usd": 0},
        "cumulative_total": total,
    }))
    live = client.get(f"/api/research/status/{PID}").get_json()["data"]["live"]
    assert live["spend_so_far"]["available"] is False
    assert live["budget"]["remaining_tokens"] is None


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
