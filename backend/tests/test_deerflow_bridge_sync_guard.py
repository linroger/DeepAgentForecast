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
from pathlib import Path
import runpy

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
    _write(
        bridge_dir / "skills" / "actor-ontology-research" / "references" / "details.md",
        "lazy reference\n",
    )

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    deployed_skill = deployed_dir / "skills" / "public" / "actor-ontology-research" / "SKILL.md"
    assert deployed_skill.read_text() == "NEW cast-cap rule\n"
    assert (
        deployed_dir / "skills" / "public" / "actor-ontology-research"
        / "references" / "details.md"
    ).read_text() == "lazy reference\n"


def test_syncs_config_reflected_tool_modules(tmp_path, monkeypatch):
    """Bridge tool modules are imported from deployed bare-module paths.
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
    _write(bridge_dir / "research_budget.py", "# research_budget body\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    assert (deployed_dir / "market_tools.py").read_text() == "# NEW market_tools\n"
    assert (deployed_dir / "search_tools.py").read_text() == "# search_tools body\n"
    assert (deployed_dir / "cached_fetch.py").read_text() == "# cached_fetch body\n"
    assert (deployed_dir / "research_budget.py").read_text() == "# research_budget body\n"


def test_syncs_tracked_middleware_overlay(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")
    _write(
        bridge_dir / "patches" / "middlewares" / "model_concurrency_middleware.py",
        "# tracked exact-call lease\n",
    )
    deployed_mw = (
        deployed_dir / "backend" / "packages" / "harness" / "deerflow"
        / "agents" / "middlewares")
    _write(deployed_mw / "model_concurrency_middleware.py", "# stale\n")

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    assert (deployed_mw / "model_concurrency_middleware.py").read_text() == (
        "# tracked exact-call lease\n")


def test_sync_applies_tracked_lead_agent_null_trim_overlay(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")

    real_overlay = (
        Path(__file__).resolve().parents[2]
        / "deerflow_bridge" / "patches" / "apply_lead_agent_overlays.py"
    )
    _write(
        bridge_dir / "patches" / "apply_lead_agent_overlays.py",
        real_overlay.read_text(encoding="utf-8"),
    )
    lead_agent = (
        deployed_dir / "backend" / "packages" / "harness" / "deerflow"
        / "agents" / "lead_agent" / "agent.py"
    )
    _write(
        lead_agent,
        '''def factory(config, model, trigger, keep):
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize
    return kwargs
''',
    )

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(repo_root / "backend" / "app" / "services" / "pipeline_orchestrator.py"),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    deployed = lead_agent.read_text(encoding="utf-8")
    assert '"trim_tokens_to_summarize": config.trim_tokens_to_summarize' in deployed
    assert "if config.trim_tokens_to_summarize is not None" not in deployed


def test_sync_applies_model_factory_metadata_overlay(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")
    real_overlay = (
        Path(__file__).resolve().parents[2]
        / "deerflow_bridge" / "patches" / "apply_model_factory_overlays.py"
    )
    _write(
        bridge_dir / "patches" / "apply_model_factory_overlays.py",
        real_overlay.read_text(encoding="utf-8"),
    )
    factory = (
        deployed_dir / "backend" / "packages" / "harness" / "deerflow"
        / "models" / "factory.py"
    )
    _write(
        factory,
        '''def create(model_config):
    return model_config.model_dump(
        exclude={
            "supports_vision",
        },
    )
''',
    )

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(
            repo_root / "backend" / "app" / "services"
            / "pipeline_orchestrator.py"
        ),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    deployed = factory.read_text(encoding="utf-8")
    assert '"context_window_tokens"' in deployed
    once = deployed
    _sync_deerflow_bridge_if_stale(str(deployed_dir))
    assert factory.read_text(encoding="utf-8") == once


def test_syncs_tracked_subagent_executor_overlay(tmp_path, monkeypatch):
    repo_root = tmp_path
    bridge_dir = repo_root / "deerflow_bridge"
    deployed_dir = repo_root / "deer-flow"
    _write(bridge_dir / "deerflow_research.py", "same\n")
    _write(deployed_dir / "deerflow_research.py", "same\n")
    real_overlay = (
        Path(__file__).resolve().parents[2]
        / "deerflow_bridge" / "patches" / "apply_subagent_overlays.py"
    )
    _write(
        bridge_dir / "patches" / "apply_subagent_overlays.py",
        real_overlay.read_text(encoding="utf-8"),
    )
    deployed_subagents = (
        deployed_dir / "backend" / "packages" / "harness" / "deerflow"
        / "subagents")
    _write(
        deployed_subagents / "executor.py",
        '''class SubagentExecutor:
    tracing_and_session_behavior = "preserve-me"

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Upstream body remains byte-for-byte after its signature."""
        return self.tracing_and_session_behavior
''',
    )

    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.__file__",
        str(
            repo_root / "backend" / "app" / "services"
            / "pipeline_orchestrator.py"
        ),
    )

    _sync_deerflow_bridge_if_stale(str(deployed_dir))

    deployed = (deployed_subagents / "executor.py").read_text()
    assert "async_subagent_lifecycle_lease" in deployed
    assert "async def _aexecute_under_lease" in deployed
    assert 'tracing_and_session_behavior = "preserve-me"' in deployed
    assert "Upstream body remains byte-for-byte" in deployed

    # Idempotence: the drift guard may run before every outer lane.
    once = deployed
    _sync_deerflow_bridge_if_stale(str(deployed_dir))
    assert (deployed_subagents / "executor.py").read_text() == once


def test_subagent_overlay_preserves_current_vendor_observability(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    vendor = (
        repo_root / "deer-flow-2.0.0" / "backend" / "packages"
        / "harness" / "deerflow" / "subagents" / "executor.py"
    )
    overlay = (
        repo_root / "deerflow_bridge" / "patches"
        / "apply_subagent_overlays.py"
    )
    deployed = (
        tmp_path / "backend" / "packages" / "harness" / "deerflow"
        / "subagents" / "executor.py"
    )
    _write(deployed, vendor.read_text(encoding="utf-8"))

    module = runpy.run_path(str(overlay))
    assert module["apply"](tmp_path) == "applied"
    assert module["apply"](tmp_path) == "already_applied"

    source = deployed.read_text(encoding="utf-8")
    assert "async_subagent_lifecycle_lease" in source
    for preserved in (
        "build_tracing_callbacks",
        "inject_langfuse_metadata",
        "checkpointer=False",
        "user_id=self.user_id",
    ):
        assert preserved in source


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
