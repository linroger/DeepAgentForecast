"""SPA static serving (TASK 1): the Flask backend serves the built frontend.

create_app must host frontend/dist at the root URL space:
  - '/' serves index.html;
  - unknown non-API paths fall back to index.html (Vue Router history mode);
  - real files under dist (e.g. /assets/*) are served as-is;
  - unknown '/api/*' paths stay JSON 404 (never HTML);
  - a missing dist degrades to a JSON hint (200) instead of crashing.

The dist location is overridable via the FRONTEND_DIST env var so these tests
run against a throwaway tmp_path build, never the real frontend/dist.
"""

import json
import os

import pytest


@pytest.fixture()
def dist_dir(tmp_path):
    """A minimal fake Vite build: index.html + one hashed asset."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>MiroFish</title><div id=app>SPA_INDEX_SENTINEL</div>",
        encoding="utf-8",
    )
    (dist / "assets" / "app-abc123.js").write_text(
        "console.log('ASSET_SENTINEL')", encoding="utf-8",
    )
    return dist


@pytest.fixture()
def client(dist_dir, monkeypatch):
    monkeypatch.setenv("FRONTEND_DIST", str(dist_dir))
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_root_serves_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "SPA_INDEX_SENTINEL" in resp.get_data(as_text=True)


def test_client_route_falls_back_to_index_html(client):
    """History-mode deep link (no such file on disk) must serve the SPA shell."""
    resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "SPA_INDEX_SENTINEL" in resp.get_data(as_text=True)


def test_existing_asset_served_verbatim(client):
    resp = client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "ASSET_SENTINEL" in resp.get_data(as_text=True)
    assert "SPA_INDEX_SENTINEL" not in resp.get_data(as_text=True)


def test_unknown_api_route_is_json_404_not_html(client):
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    assert resp.is_json
    body = json.loads(resp.get_data(as_text=True))
    assert body.get("success") is False
    assert "<html" not in resp.get_data(as_text=True).lower()


def test_health_endpoint_still_wins_over_spa(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json().get("status") == "ok"


def test_missing_dist_returns_json_hint_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    assert resp.is_json
    assert "npm run build" in resp.get_json().get("hint", "")


def test_frontend_dist_default_is_repo_frontend_dist(monkeypatch):
    monkeypatch.delenv("FRONTEND_DIST", raising=False)
    from app import _frontend_dist_dir
    path = _frontend_dist_dir()
    assert os.path.isabs(path)
    assert path.endswith(os.path.join("frontend", "dist"))
