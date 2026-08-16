"""GET /api/simulation/<id>/trajectory — 决策通道轨迹端点契约。

Step3 实时可视化按详细状态轮询节奏读取 world_state_trajectory.json；这些测试
钉住端点的安全与容错姿态：路径遏制（越界 id 一律 404，绝不读到模拟根目录外）、
拒绝 symlink、文件缺失 404（早期轮次属正常态）、happy path 透传轨迹行、
schema 容错（dict 包装或裸列表、非 dict 行剔除）、行数上限截断与文件大小上限。
"""

import json
import os

import pytest

from app.config import Config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _write_trajectory(tmp_path, sim_id, payload, filename="world_state_trajectory.json"):
    sim_dir = tmp_path / sim_id
    sim_dir.mkdir(parents=True, exist_ok=True)
    target = sim_dir / filename
    target.write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


ROWS = [
    {"round": 0, "shares": {"A": 0.5, "B": 0.5}},
    {"round": 1, "shares": {"A": 0.6, "B": 0.4}, "period_end": "2026-08-01"},
]


# ---------------------------------------------------------------- happy path

def test_happy_path_returns_rows_and_scalar_metadata(client, tmp_path):
    _write_trajectory(tmp_path, "sim_ok", {
        "trajectory": ROWS,
        "schema_version": 3,
        "converged": True,
        # 非标量元数据绝不透传
        "internals": {"big": "blob"},
    })
    resp = client.get("/api/simulation/sim_ok/trajectory")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    data = body["data"]
    assert data["trajectory"] == ROWS
    assert data["rows_count"] == 2
    assert data["total_rows"] == 2
    assert data["truncated"] is False
    assert data["schema_version"] == 3
    assert data["converged"] is True
    assert "internals" not in data


def test_bare_list_payload_and_non_dict_rows_tolerated(client, tmp_path):
    _write_trajectory(tmp_path, "sim_list", [ROWS[0], "junk", 42, ROWS[1]])
    resp = client.get("/api/simulation/sim_list/trajectory")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["trajectory"] == ROWS
    assert data["rows_count"] == 2


# ---------------------------------------------------------------- missing = 404

def test_missing_file_returns_404(client, tmp_path):
    (tmp_path / "sim_nofile").mkdir()
    resp = client.get("/api/simulation/sim_nofile/trajectory")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_missing_sim_dir_returns_404(client):
    resp = client.get("/api/simulation/sim_nowhere/trajectory")
    assert resp.status_code == 404


# ---------------------------------------------------------------- containment

def test_traversal_ids_are_contained(client, tmp_path):
    # 根外的诱饵文件：任何越界 id 都不得读到它
    (tmp_path.parent / "world_state_trajectory.json").write_text(
        json.dumps({"trajectory": ROWS}), encoding="utf-8")
    from app.api.simulation import get_simulation_trajectory
    for bad_id in ("..", ".", "", "x/..", "../x", "..\\x", "a\x00b"):
        with client.application.test_request_context():
            rv = get_simulation_trajectory(bad_id)
        assert rv[1] == 404, f"id {bad_id!r} 未被遏制"


def test_traversal_via_routing_layer_is_rejected(client, tmp_path):
    (tmp_path.parent / "world_state_trajectory.json").write_text(
        json.dumps({"trajectory": ROWS}), encoding="utf-8")
    resp = client.get("/api/simulation/../trajectory")
    assert resp.status_code == 404


def test_symlinked_trajectory_rejected(client, tmp_path):
    outside = tmp_path.parent / "outside_trajectory.json"
    outside.write_text(json.dumps({"trajectory": ROWS}), encoding="utf-8")
    sim_dir = tmp_path / "sim_link"
    sim_dir.mkdir()
    os.symlink(outside, sim_dir / "world_state_trajectory.json")
    resp = client.get("/api/simulation/sim_link/trajectory")
    assert resp.status_code == 404


# ---------------------------------------------------------------- bounded body

def test_row_cap_keeps_latest_rows_and_flags_truncation(client, tmp_path, monkeypatch):
    import app.api.simulation as sim_api
    monkeypatch.setattr(sim_api, "TRAJECTORY_MAX_ROWS", 3)
    rows = [{"round": i, "shares": {"A": 1.0}} for i in range(8)]
    _write_trajectory(tmp_path, "sim_cap", {"trajectory": rows})
    resp = client.get("/api/simulation/sim_cap/trajectory")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["truncated"] is True
    assert data["total_rows"] == 8
    assert data["rows_count"] == 3
    assert [r["round"] for r in data["trajectory"]] == [5, 6, 7]  # 保留最新行


def test_oversized_file_rejected(client, tmp_path, monkeypatch):
    import app.api.simulation as sim_api
    monkeypatch.setattr(sim_api, "TRAJECTORY_MAX_BYTES", 16)
    _write_trajectory(tmp_path, "sim_big", {"trajectory": ROWS})
    resp = client.get("/api/simulation/sim_big/trajectory")
    assert resp.status_code == 413
    assert resp.get_json()["success"] is False


def test_malformed_json_returns_500_without_traceback_leak(client, tmp_path):
    _write_trajectory(tmp_path, "sim_bad", "not json {{{")
    resp = client.get("/api/simulation/sim_bad/trajectory")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["success"] is False
    assert "traceback" not in body  # 解析失败走专用分支，不吐 traceback
