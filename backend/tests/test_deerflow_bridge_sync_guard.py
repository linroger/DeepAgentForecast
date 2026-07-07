"""Offline unit tests for the DeerFlow bridge/deployment drift guard.

Live-surfaced 2026-07-03: a forecast run on MiniMax completed its research
stage with a 28-actor cast (violating ACTOR_CAST_MAX=20) because the deployed
copy at $DEERFLOW_DIR/deerflow_research.py had not been re-synced from
deerflow_bridge/ since a whole session's worth of fixes landed there (setup.sh
line ~579 only performs this cp when explicitly re-run). _sync_deerflow_bridge_if_stale
closes that gap by hash-comparing the bridge source against the deployed copy
immediately before every research subprocess launch and auto-resyncing on drift.
"""

import hashlib
import os

from app.services.pipeline_orchestrator import _sync_deerflow_bridge_if_stale


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_syncs_stale_deployed_script(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "# NEW content with enforce_actor_cast\n")
    _write(deployed_dir / "deerflow_research.py", "# OLD stale content\n")

    # _sync_deerflow_bridge_if_stale derives repo_root from this module's own
    # __file__ (three parents up); patch that instead of passing repo_root in.
    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    assert (deployed_dir / "deerflow_research.py").read_text() == "# NEW content with enforce_actor_cast\n"
    assert _digest(bridge_dir / "deerflow_research.py") == _digest(deployed_dir / "deerflow_research.py")


def test_noop_when_already_in_sync(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    same_content = "# identical content\n"
    _write(bridge_dir / "deerflow_research.py", same_content)
    _write(deployed_dir / "deerflow_research.py", same_content)
    deployed_script = deployed_dir / "deerflow_research.py"
    mtime_before = deployed_script.stat().st_mtime_ns

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    # Content unchanged and file not rewritten (mtime stable) when already in sync.
    assert deployed_script.read_text() == same_content
    assert deployed_script.stat().st_mtime_ns == mtime_before


def test_syncs_skill_files_too(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")
    _write(bridge_dir / "skills" / "actor-ontology-research" / "SKILL.md", "NEW cast-cap rule\n")
    _write(deployed_dir / "skills" / "public" / "actor-ontology-research" / "SKILL.md", "OLD rule\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    deployed_skill = deployed_dir / "skills" / "public" / "actor-ontology-research" / "SKILL.md"
    assert deployed_skill.read_text() == "NEW cast-cap rule\n"


def test_syncs_config_reflected_tool_modules(tmp_path, monkeypatch):
    """market_tools/search_tools/cached_fetch are registered in config.yaml as BARE
    module names and imported via sys.path[0]==deer-flow/; a bridge-only edit must
    re-sync them to the deployed dir or web_search/web_fetch/prediction_market tools
    fail to import at runtime (regression: search_tools/cached_fetch were absent from
    deer-flow/ entirely while config.yaml referenced them)."""
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")
    # market_tools already deployed but stale; search_tools/cached_fetch missing entirely.
    _write(bridge_dir / "market_tools.py", "# NEW market_tools\n")
    _write(deployed_dir / "market_tools.py", "# OLD market_tools\n")
    _write(bridge_dir / "search_tools.py", "# search_tools body\n")
    _write(bridge_dir / "cached_fetch.py", "# cached_fetch body\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    assert (deployed_dir / "market_tools.py").read_text() == "# NEW market_tools\n"
    assert (deployed_dir / "search_tools.py").read_text() == "# search_tools body\n"
    assert (deployed_dir / "cached_fetch.py").read_text() == "# cached_fetch body\n"


def test_degrades_safely_when_bridge_dir_missing(tmp_path, monkeypatch):
    """No deerflow_bridge/ source (e.g. pure production deploy) -> silent no-op, never raises."""
    repo_root = tmp_path
    deployed_dir = repo_root / "deer-flow"
    _write(deployed_dir / "deerflow_research.py", "whatever\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    # Must not raise even though deerflow_bridge/ doesn't exist at all.
    _sync_deerflow_bridge_if_stale(str(deployed_dir))
    assert (deployed_dir / "deerflow_research.py").read_text() == "whatever\n"


def test_degrades_safely_on_unexpected_error(tmp_path, monkeypatch):
    """Any exception inside the guard is swallowed - it must never break a pipeline launch."""
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "x\n")
    _write(deployed_dir / "deerflow_research.py", "y\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    def _boom(*a, **kw):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("app.services.pipeline_orchestrator.shutil.copyfile", _boom)

    # Should not raise - degrades to a logged warning only.
    _sync_deerflow_bridge_if_stale(str(deployed_dir))
