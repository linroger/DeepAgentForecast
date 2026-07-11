"""Offline contracts for parallel evidence lanes -> one global synthesis."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE_DIR = _REPO_ROOT / "deerflow_bridge"
if str(_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_DIR))

import deerflow_research as dr  # noqa: E402
from app.services import pipeline_orchestrator as po  # noqa: E402


class _Log:
    def __init__(self):
        self.rows = []

    def write(self, kind, message):
        self.rows.append((kind, message))


def test_evidence_pack_is_lossless_and_exact_deduplicated():
    pack = dr.render_evidence_pack(["alpha evidence", "alpha evidence", "beta"])

    assert pack.startswith("# Internal Evidence Lane Pack")
    assert pack.count("alpha evidence") == 1
    assert pack.count("<!-- evidence-block:") == 2
    assert dr.parse_evidence_pack(pack) == ["alpha evidence", "beta"]


def test_manifest_loads_rooted_lanes_and_rejects_escape(tmp_path):
    lane = tmp_path / "track_1" / "evidence_pack.md"
    lane.parent.mkdir()
    lane.write_text("evidence " * 100, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "lanes": [{"title": "Base rates", "path": "track_1/evidence_pack.md"}],
        "sources": [{"url": "https://example.gov/source"}],
    }), encoding="utf-8")

    parts, sources = dr.load_evidence_manifest(manifest)
    assert len(parts) == 1
    assert parts[0].startswith("<!-- evidence-lane:1 title:Base rates")
    assert "evidence evidence" in parts[0]
    assert sources == [{"url": "https://example.gov/source"}]

    manifest.write_text(json.dumps({
        "version": 1,
        "lanes": [{"path": "../outside.md"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes manifest root"):
        dr.load_evidence_manifest(manifest)


def test_version2_manifest_verifies_every_lane_and_source_ledger(tmp_path):
    lane_dir = tmp_path / "track_1"
    lane_dir.mkdir()
    evidence_path = lane_dir / "evidence_pack.md"
    sources_path = lane_dir / "sources.json"
    evidence_path.write_text(
        dr.render_evidence_pack(["verified lane evidence"]), encoding="utf-8")
    sources = [{"url": "https://example.gov/source", "title": "Official"}]
    sources_path.write_text(json.dumps(sources), encoding="utf-8")
    evidence_bytes = evidence_path.read_bytes()
    source_bytes = sources_path.read_bytes()
    canonical_sources = json.dumps(
        sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 2,
        "lanes": [{
            "title": "Verified",
            "path": "track_1/evidence_pack.md",
            "bytes": len(evidence_bytes),
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "sources_path": "track_1/sources.json",
            "sources_bytes": len(source_bytes),
            "sources_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }],
        "sources": sources,
        "sources_count": 1,
        "sources_sha256": hashlib.sha256(canonical_sources).hexdigest(),
    }), encoding="utf-8")

    parts, loaded_sources = dr.load_evidence_manifest(manifest)
    assert "verified lane evidence" in parts[0]
    assert loaded_sources == sources

    evidence_path.write_text("post-manifest tamper", encoding="utf-8")
    with pytest.raises(ValueError, match="(?:byte count|fingerprint) mismatch"):
        dr.load_evidence_manifest(manifest)


def test_manifest_routes_blocks_round_robin_and_remaps_lane_citations(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHESIS_EVIDENCE_BLOCK_CHARS", "4000")
    monkeypatch.setenv("RESEARCH_CITATION_INDEX_MAX", "100")
    global_sources = [
        {"url": "https://one.gov/a", "title": "Lane one source"},
        {"url": "https://two.gov/b", "title": "Lane two source"},
    ]
    lanes = []
    for lane, source in enumerate(global_sources, start=1):
        lane_dir = tmp_path / f"track_{lane}"
        lane_dir.mkdir()
        claim = (
            f"LANE_{lane}_OPEN target-{lane} fact [S1] "
            + (f"lane-{lane} filler " * 700)
        )
        unique = "laneoneexclusive" if lane == 1 else "lanetwoexclusive"
        tail = (
            f"TARGET_TAIL_{lane} {unique} decisive evidence [S1] "
            "UNMAPPED [S99]"
        )
        (lane_dir / "evidence_pack.md").write_text(
            dr.render_evidence_pack([claim, tail]), encoding="utf-8")
        (lane_dir / "sources.json").write_text(
            json.dumps([source]), encoding="utf-8")
        lanes.append({
            "title": f"Lane {lane}",
            "path": f"track_{lane}/evidence_pack.md",
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "lanes": lanes,
        "sources": global_sources,
    }), encoding="utf-8")

    parts, sources = dr.load_evidence_manifest(manifest)

    assert sources == global_sources
    # Chunks are interleaved by lane rather than exhausting lane 1 first.
    assert "evidence-lane:1" in parts[0]
    assert "evidence-lane:2" in parts[1]
    lane_one = "\n".join(p for p in parts if "evidence-lane:1" in p)
    lane_two = "\n".join(p for p in parts if "evidence-lane:2" in p)
    assert "LANE_1_OPEN target-1 fact [S1]" in lane_one
    assert "LANE_2_OPEN target-2 fact [S2]" in lane_two
    assert "[S99]" not in lane_one + lane_two

    # A small outline allowance still contains both lanes. Previously the head
    # of the oversized first lane consumed the entire allowance.
    outline_context = dr.build_stratified_outline_context(
        parts, 1200, max_blocks=8)
    assert "LANE_1_OPEN" in outline_context
    assert "LANE_2_OPEN" in outline_context

    # The second evidence block remains independently routable after a large
    # first block, rather than disappearing behind one prefix truncation.
    routed = dr.pack_context_for_section(
        parts, "lanetwoexclusive", cap=8000, max_blocks=4)
    assert "TARGET_TAIL_2" in routed
    assert "TARGET_TAIL_1" not in routed


def test_lane_marker_without_local_url_is_stripped_not_misassigned():
    global_entries = dr.build_citation_index([
        {"url": "https://one.gov/a", "title": "First"},
        {"url": "https://two.gov/b", "title": "Second"},
    ])
    remapped = dr.remap_lane_citations(
        "grounded [S1]; unknown [S2]",
        [{"url": "https://two.gov/b", "title": "Second"}],
        global_entries,
    )
    assert remapped == "grounded [S2]; unknown "


def test_evidence_source_persistence_replaces_stale_rows_with_empty(tmp_path):
    source_path = tmp_path / dr.SOURCES_FILENAME
    source_path.write_text(json.dumps([
        {"url": "https://stale.example/old"},
    ]), encoding="utf-8")

    dr.persist_evidence_sources(tmp_path, [])

    assert json.loads(source_path.read_text(encoding="utf-8")) == []


def test_manifest_sources_keep_global_ids_excerpts_and_filter_denied(monkeypatch):
    monkeypatch.delenv("RESEARCH_ALLOW_LOW_QUALITY_SOURCES", raising=False)
    monkeypatch.delenv("RESEARCH_SOURCE_DENY_DOMAINS", raising=False)
    count = dr.seed_manifest_sources([
        {
            "url": "https://www.sec.gov/filing",
            "title": "Official filing",
            "excerpt": "semiconductor revenue guidance",
        },
        {
            "url": "https://economicsummarizer.com/rewrite",
            "excerpt": "low-quality summary",
        },
    ])

    index = dr.build_citation_index(dr._FETCHED_SOURCES)
    assert count == 1
    assert index == [{
        "n": 1,
        "title": "Official filing",
        "url": "https://www.sec.gov/filing",
        "excerpt": "semiconductor revenue guidance",
    }]


def test_shared_synthesis_boundary_invokes_multipart_once(monkeypatch):
    monkeypatch.setenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", "0")
    monkeypatch.setattr(dr, "_multipart_synthesis_enabled", lambda _depth: True)
    calls = []

    def fake_multipart(question, language, depth, model, blocks, ai_parts, context, plog):
        calls.append((question, list(blocks), context))
        return "# Global dossier\n\nIntegrated evidence."

    monkeypatch.setattr(dr, "synthesize_multipart", fake_multipart)
    result = dr.synthesize_from_evidence_parts(
        ["lane one " * 100, "lane two " * 100],
        ["lane one", "lane two"],
        "What happens?", "English", "claude", _Log(), "deep",
    )

    assert result.startswith("# Global dossier")
    assert len(calls) == 1
    assert calls[0][1] == [
        ("lane one " * 100).strip(), ("lane two " * 100).strip()]


def test_single_call_synthesis_fallback_samples_all_lanes(monkeypatch):
    monkeypatch.setenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", "0")
    monkeypatch.setattr(dr, "_multipart_synthesis_enabled", lambda _depth: False)
    monkeypatch.setattr(dr, "_synthesis_context_cap", lambda *args, **kwargs: 1200)
    captured = {}

    models_module = types.ModuleType("deerflow.models")
    models_module.create_chat_model = lambda *args, **kwargs: object()
    deerflow_module = types.ModuleType("deerflow")
    deerflow_module.models = models_module
    messages_module = types.ModuleType("langchain_core.messages")

    class HumanMessage:
        def __init__(self, content):
            self.content = content

    messages_module.HumanMessage = HumanMessage
    langchain_module = types.ModuleType("langchain_core")
    langchain_module.messages = messages_module
    monkeypatch.setitem(sys.modules, "deerflow", deerflow_module)
    monkeypatch.setitem(sys.modules, "deerflow.models", models_module)
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_module)
    monkeypatch.setitem(sys.modules, "langchain_core.messages", messages_module)

    def _invoke(_model, messages):
        captured["prompt"] = messages[0].content
        return types.SimpleNamespace(content="# Integrated report")

    monkeypatch.setattr(dr, "_invoke_model", _invoke)
    result = dr.synthesize_from_evidence_parts(
        ["LANE_ONE " + "a" * 5000, "LANE_TWO " + "b" * 5000],
        [], "What happens?", "English", "test-model", _Log(), "standard",
    )

    assert result == "# Integrated report"
    assert "LANE_ONE" in captured["prompt"]
    assert "LANE_TWO" in captured["prompt"]


def _configure_global_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SYNTHESIS", True, raising=False)
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_SUBAGENT_CAP", 3, raising=False)
    monkeypatch.setattr(po.Config, "RESEARCH_GLOBAL_MODEL_CONCURRENCY", 4, raising=False)
    monkeypatch.setattr(po.Config, "RESEARCH_SUBAGENTS_PER_TRACK_MAX", 1, raising=False)
    monkeypatch.setattr(po.Config, "DEERFLOW_SUBAGENTS", True, raising=False)
    monkeypatch.setattr(po.Config, "DEERFLOW_DUAL_TRACK", True, raising=False)
    monkeypatch.setattr(po.Config, "DEERFLOW_MODEL", "claude", raising=False)
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _path: None)
    monkeypatch.setattr(
        po.PipelineManager, "save", classmethod(lambda cls, state: None))


def test_three_outer_lanes_produce_one_global_report_not_three(tmp_path, monkeypatch):
    _configure_global_mode(monkeypatch, tmp_path)
    calls = []

    def fake_run(prompt, handoff_dir, **kwargs):
        folder = Path(handoff_dir)
        folder.mkdir(parents=True, exist_ok=True)
        if kwargs.get("evidence_only"):
            lane = int(folder.name.split("_")[-1])
            calls.append(("evidence", lane))
            evidence = f"# Internal Evidence Lane Pack\n\nlane {lane} evidence " * 50
            (folder / "evidence_pack.md").write_text(evidence, encoding="utf-8")
            sources = [{
                "url": f"https://example.gov/source-{lane}",
                "title": f"Source {lane}",
                "excerpt": f"lane {lane} excerpt",
                "tier": "S1",
            }]
            (folder / "sources.json").write_text(
                json.dumps(sources), encoding="utf-8")
            dossier = "actor evidence " * 100 if lane == 1 else ""
            if dossier:
                (folder / "actor_dossier.md").write_text(dossier, encoding="utf-8")
            return {
                "report": evidence,
                "evidence_pack": evidence,
                "report_path": str(folder / "evidence_pack.md"),
                "actor_dossier": dossier,
                "actors": None,
                "sources": sources,
                "timeline": None,
                "exit_code": 0,
                "research_telemetry": {
                    "model": "claude", "depth": "deep",
                    "tokens_in": 10, "tokens_out": 2, "tokens_total": 12,
                    "tool_calls": 3, "results": 3, "wall_s": 10 + lane,
                },
            }
        assert kwargs.get("synthesis_manifest_path")
        manifest = json.loads(Path(
            kwargs["synthesis_manifest_path"]).read_text(encoding="utf-8"))
        assert len(manifest["lanes"]) == 3
        calls.append(("global", len(manifest["lanes"])))
        report = "# Global forecast dossier\n\n" + "integrated outcome evidence " * 40
        actors = {"actors": [{"name": "Core Actor"}], "relationships": []}
        (folder / "research_report.md").write_text(report, encoding="utf-8")
        (folder / "actors.json").write_text(json.dumps(actors), encoding="utf-8")
        (folder / "sources.json").write_text(
            json.dumps(manifest["sources"]), encoding="utf-8")
        (folder / "meta.json").write_text("{}", encoding="utf-8")
        return {
            "report": report,
            "report_path": str(folder / "research_report.md"),
            "actor_dossier": "actor evidence " * 100,
            "actors": actors,
            "sources": manifest["sources"],
            "timeline": None,
            "exit_code": 0,
            "research_telemetry": {
                "model": "claude", "depth": "deep",
                "tokens_in": 5, "tokens_out": 5, "tokens_total": 10,
                "tool_calls": 0, "results": 0, "wall_s": 5,
            },
        }

    monkeypatch.setattr(
        po.DeerFlowResearchRunner, "run", staticmethod(fake_run))
    handoff = tmp_path / "handoff"
    state = po.PipelineState(
        pipeline_id="pipe_global_synthesis",
        prompt="Forecast the outcome",
        handoff_dir=str(handoff),
        options={
            "depth": "deep",
            "research_language": "English",
            "research_model": "claude",
        },
    )
    result = po.PipelineOrchestrator()._run_parallel_research_tracks(
        state, str(handoff), lambda _pct, _msg: None, 3)

    assert sorted(calls) == [
        ("evidence", 1), ("evidence", 2), ("evidence", 3), ("global", 3)]
    assert result["report"].startswith("# Global forecast dossier")
    assert "# Track" not in result["report"]
    assert result["research_telemetry"]["global_synthesis_runs"] == 1
    assert result["research_telemetry"]["tokens_total"] == 46
    assert result["research_telemetry"]["wall_s"] == 18.0
    meta = json.loads((handoff / "meta.json").read_text(encoding="utf-8"))
    assert meta["parallel_research"]["architecture"] == (
        "parallel_evidence_single_global_synthesis")
    assert len(json.loads((handoff / "evidence_synthesis_manifest.json").read_text())[
        "lanes"]) == 3
    manifest = json.loads((handoff / "evidence_synthesis_manifest.json").read_text())
    assert manifest["version"] == 2
    assert manifest["sources_count"] == 3
    assert all(row.get("sha256") and row.get("sources_sha256") for row in manifest["lanes"])


def test_global_synthesis_failure_retries_manifest_only_once(tmp_path, monkeypatch):
    _configure_global_mode(monkeypatch, tmp_path)
    calls = []

    def fake_run(prompt, handoff_dir, **kwargs):
        folder = Path(handoff_dir)
        folder.mkdir(parents=True, exist_ok=True)
        if kwargs.get("evidence_only"):
            lane = int(folder.name.split("_")[-1])
            calls.append(f"evidence-{lane}")
            evidence = "# Internal Evidence Lane Pack\n\n" + "evidence " * 80
            sources = [{
                "url": f"https://example.gov/{lane}", "tier": "S1",
            }]
            (folder / "evidence_pack.md").write_text(evidence, encoding="utf-8")
            (folder / "sources.json").write_text(
                json.dumps(sources), encoding="utf-8")
            return {
                "report": evidence, "report_path": str(folder / "evidence_pack.md"),
                "actor_dossier": "", "actors": None, "sources": sources,
                "timeline": None, "exit_code": 0,
                "research_telemetry": {"wall_s": 4, "tokens_total": 4},
            }
        if kwargs.get("synthesis_manifest_path"):
            manifest_calls = sum(1 for call in calls if call.startswith("global-"))
            if manifest_calls == 0:
                calls.append("global-failed")
                raise RuntimeError("simulated transient global writer outage")
            calls.append("global-retry")
        else:  # pragma: no cover - full research fallback is forbidden
            raise AssertionError("global failure must never resume/restart a lane")
        assert not kwargs.get("resume")
        report = "# Recovered global dossier\n\n" + "forecast evidence " * 50
        (folder / "research_report.md").write_text(report, encoding="utf-8")
        (folder / "meta.json").write_text("{}", encoding="utf-8")
        return {
            "report": report, "report_path": str(folder / "research_report.md"),
            "actor_dossier": "", "actors": None, "sources": [],
            "timeline": None, "exit_code": 0,
            "research_telemetry": {"wall_s": 2, "tokens_total": 2},
        }

    monkeypatch.setattr(
        po.DeerFlowResearchRunner, "run", staticmethod(fake_run))
    handoff = tmp_path / "handoff"
    state = po.PipelineState(
        pipeline_id="pipe_global_fallback",
        prompt="Forecast the outcome",
        handoff_dir=str(handoff),
        options={"depth": "deep", "research_model": "claude"},
    )

    result = po.PipelineOrchestrator()._run_parallel_research_tracks(
        state, str(handoff), lambda _pct, _msg: None, 2)

    assert calls.count("global-failed") == 1
    assert calls.count("global-retry") == 1
    assert calls.count("evidence-1") == 1 and calls.count("evidence-2") == 1
    assert result["report"].startswith("# Recovered global dossier")
    assert result["research_telemetry"]["global_synthesis_runs"] == 2
    assert state.options["parallel_research"]["global_synthesis_attempts"] == 2
    assert "global_synthesis_fallback" not in state.options
    assert not list(handoff.glob(".global-synthesis-*"))


def test_global_synthesis_double_failure_preserves_evidence_and_fails_closed(
        tmp_path, monkeypatch):
    _configure_global_mode(monkeypatch, tmp_path)
    calls = []

    def fake_run(prompt, handoff_dir, **kwargs):
        folder = Path(handoff_dir)
        folder.mkdir(parents=True, exist_ok=True)
        if kwargs.get("evidence_only"):
            lane = int(folder.name.split("_")[-1])
            calls.append(f"evidence-{lane}")
            evidence = "# Internal Evidence Lane Pack\n\n" + "evidence " * 80
            (folder / "evidence_pack.md").write_text(evidence, encoding="utf-8")
            (folder / "sources.json").write_text("[]", encoding="utf-8")
            return {
                "report": evidence, "report_path": str(folder / "evidence_pack.md"),
                "actor_dossier": "", "actors": None, "sources": [],
                "timeline": None, "exit_code": 0,
                "research_telemetry": {"wall_s": 4, "tokens_total": 4},
            }
        assert kwargs.get("synthesis_manifest_path")
        assert not kwargs.get("resume")
        calls.append("global-failed")
        raise RuntimeError("persistent global outage")

    monkeypatch.setattr(
        po.DeerFlowResearchRunner, "run", staticmethod(fake_run))
    handoff = tmp_path / "handoff"
    state = po.PipelineState(
        pipeline_id="pipe_global_double_failure",
        prompt="Forecast the outcome", handoff_dir=str(handoff),
        options={"depth": "deep", "research_model": "claude"},
    )

    with pytest.raises(RuntimeError, match="拒绝回退"):
        po.PipelineOrchestrator()._run_parallel_research_tracks(
            state, str(handoff), lambda _pct, _msg: None, 2)

    assert calls.count("global-failed") == 2
    assert calls.count("evidence-1") == 1 and calls.count("evidence-2") == 1
    assert (handoff / "track_1" / "evidence_pack.md").exists()
    assert (handoff / "track_2" / "evidence_pack.md").exists()
    failure = state.options["global_synthesis_failure"]
    assert failure["global_synthesis_attempts"] == 2
    assert failure["evidence_lanes_consumed"] == 2
    assert not (handoff / "research_report.md").exists()


def test_research_contract_replaces_stale_optionals_and_detects_tamper(tmp_path):
    handoff = tmp_path / "handoff"
    source = tmp_path / "source"
    handoff.mkdir()
    source.mkdir()
    (handoff / "research_report.md").write_text("old " * 200, encoding="utf-8")
    (handoff / "contested.json").write_text('[{"old": true}]', encoding="utf-8")
    (handoff / "charts").mkdir()
    (handoff / "charts" / "old.png").write_bytes(b"old-chart")
    (source / "research_report.md").write_text("new evidence " * 100, encoding="utf-8")
    (source / "sources.json").write_text(
        '[{"url": "https://example.gov/new"}]', encoding="utf-8")
    (source / "meta.json").write_text("{}", encoding="utf-8")
    (source / "charts").mkdir()
    (source / "charts" / "new.png").write_bytes(b"new-chart")

    manifest = po._promote_research_contract(
        str(source), str(handoff), meta_patch={"parallel_research": {"survived": 2}})

    assert manifest["version"] == 1
    assert po._validate_research_contract(str(handoff)) is True
    assert not (handoff / "contested.json").exists()
    assert not (handoff / "charts" / "old.png").exists()
    assert (handoff / "charts" / "new.png").read_bytes() == b"new-chart"
    meta = json.loads((handoff / "meta.json").read_text(encoding="utf-8"))
    assert meta["parallel_research"]["survived"] == 2

    (handoff / "research_report.md").write_text("tampered", encoding="utf-8")
    assert po._validate_research_contract(str(handoff)) is False


def test_research_contract_promotion_rolls_back_mid_install(tmp_path, monkeypatch):
    handoff = tmp_path / "handoff"
    old_source = tmp_path / "old-source"
    new_source = tmp_path / "new-source"
    for folder in (handoff, old_source, new_source):
        folder.mkdir()
    old_report = "old generation evidence " * 50
    (old_source / "research_report.md").write_text(old_report, encoding="utf-8")
    (old_source / "meta.json").write_text('{"generation": "old"}', encoding="utf-8")
    po._promote_research_contract(str(old_source), str(handoff))
    assert po._validate_research_contract(str(handoff))

    (new_source / "research_report.md").write_text(
        "new generation evidence " * 50, encoding="utf-8")
    (new_source / "meta.json").write_text('{"generation": "new"}', encoding="utf-8")
    real_replace = po.os.replace

    def flaky_replace(src, dst):
        if ".research-stage-" in str(src) and str(dst).endswith("meta.json"):
            raise OSError("simulated mid-promotion failure")
        return real_replace(src, dst)

    monkeypatch.setattr(po.os, "replace", flaky_replace)
    with pytest.raises(OSError, match="mid-promotion"):
        po._promote_research_contract(str(new_source), str(handoff))

    assert (handoff / "research_report.md").read_text(encoding="utf-8") == old_report
    assert json.loads((handoff / "meta.json").read_text())["generation"] == "old"
    assert po._validate_research_contract(str(handoff)) is True


def test_research_runner_rejects_nonzero_exit_with_stale_artifact(
        tmp_path, monkeypatch):
    deerflow_dir = tmp_path / "deer-flow"
    deerflow_dir.mkdir()
    (deerflow_dir / "deerflow_research.py").write_text(
        "# test entrypoint\n", encoding="utf-8")
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    (handoff / "research_report.md").write_text(
        "stale research evidence " * 40, encoding="utf-8")

    class FailedProcess:
        pid = 9876
        stdout = []

        @staticmethod
        def poll():
            return 2

        @staticmethod
        def wait(timeout=None):
            return 2

    monkeypatch.setattr(po.Config, "DEERFLOW_DIR", str(deerflow_dir))
    monkeypatch.setattr(po.Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _path: None)
    monkeypatch.setattr(po.subprocess, "Popen", lambda *a, **k: FailedProcess())

    with pytest.raises(RuntimeError, match="exit=2"):
        po.DeerFlowResearchRunner.run(
            "forecast question", str(handoff),
            on_progress=lambda _pct, _msg: None, timeout=10,
        )
