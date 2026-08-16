"""Audit-fix regression tests for the deerflow_bridge research stage (group: research).

Covers RES-1/2/3/4/7/8/9/10/11. The bridge module is stdlib-only at import time
(deerflow imports live inside functions), so it is loaded via importlib without
running main() — there is no other pytest coverage for the bridge.
"""

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIDGE_PY = REPO / "deerflow_bridge" / "deerflow_research.py"
BRIDGE_CFG = REPO / "deerflow_bridge" / "config.yaml"
DEPLOYED_CFG = REPO / "deer-flow" / "config.yaml"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("deerflow_research_bridge", BRIDGE_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(autouse=True)
def _clean_state(mod):
    mod._reset_fetched_sources()
    yield
    mod._reset_fetched_sources()


def _plog(mod, tmp_path):
    return mod.ProgressLog(tmp_path / "progress.log")


LIVE_CONTENT = "Real page content. " * 20  # >=200 chars, no dead-fetch sentinel


def test_progress_log_reopen_appends_instead_of_overwriting_prior_attempt(mod, tmp_path):
    path = tmp_path / "progress.log"
    first = mod.ProgressLog(path)
    first.write("stage", "first attempt")
    first.close()

    second = mod.ProgressLog(path)
    second.write("stage", "recovered attempt")
    second.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("[stage] first attempt")
    assert lines[1].endswith("[stage] recovered attempt")


def test_requested_forecast_visuals_reject_generic_diagnostics(mod, monkeypatch):
    monkeypatch.setenv("RESEARCH_CHARTS_MIN", "3")
    prompt = (
        "Provide actual-data visualizations showing cost curves, deployment "
        "trajectories, regional comparisons, and published forecast revisions."
    )
    generic = [
        {"id": "actor_network"},
        {"id": "timeline"},
        {"id": "source_quality"},
    ]
    audit = mod._visual_contract_audit(prompt, generic)
    assert audit["passed"] is False
    assert audit["rendered_diagnostic_ids"] == ["actor_network", "source_quality"]
    assert audit["missing_required_ids"] == [
        "forecast_revisions", "metric_trajectories", "quant_metrics"
    ]

    actual = [
        {"id": "metric_trajectories"},
        {"id": "quant_metrics"},
        {"id": "forecast_revisions"},
    ]
    assert mod._visual_contract_audit(prompt, actual)["passed"] is True


def _actor_gap_search_receipts(mod):
    receipts = []
    for name in mod.ACTOR_INTELLIGENCE_DIMENSIONS:
        for attempt in (1, 2):
            query = f"{name} query {attempt}"
            result = f"No additional grounded {name} evidence in attempt {attempt}."
            receipt = {
                "schema_version": mod._SEARCH_RESULT_RECEIPT_SCHEMA,
                "thread_id": "research-test",
                "lane": "track-b",
                "purpose": "actor-intelligence-coverage",
                "query": query,
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "result_sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
                "result_chars": len(result),
            }
            receipt["result_id"] = mod._search_result_receipt_id(receipt)
            receipts.append(receipt)
    return receipts


def _valid_actor_dossier(mod, search_receipts):
    result_ids_by_query = {
        row["query"]: row["result_id"]
        for row in search_receipts
    }
    behavior_dimensions = {
        "identity_history",
        "incentives",
        "capabilities",
        "current_actions",
        "decision_rights_process_triggers",
    }
    dimensions = {}
    for name in mod.ACTOR_INTELLIGENCE_DIMENSIONS:
        quote = f"Core Actor has grounded {name} evidence."
        covered = name in behavior_dimensions
        dimensions[name] = {
            "status": "covered" if covered else "gap",
            "source_refs": ["https://example.com/source"] if covered else [],
            "claims": ([{
                "claim": quote,
                "evidence_type": "verified_fact",
                "claim_valid_at": "2026-07-01",
                "horizon": "current",
                "status": "observed",
                "confidence": "high",
                "source_refs": ["https://example.com/source"],
                "source_support": [{
                    "source_ref": "https://example.com/source",
                    "supporting_quote": quote,
                }],
            }] if covered else []),
            "gap": ("" if covered else {
                "reason": f"No grounded {name} evidence found.",
                "attempted_queries": [f"{name} query 1", f"{name} query 2"],
                "receipt_ids": [],
                "result_ids": [
                    result_ids_by_query[f"{name} query {attempt}"]
                    for attempt in (1, 2)
                ],
                "attempt_count": 2,
                "exhausted": True,
            }),
        }
    ledger = {
        "schema_version": mod.ACTOR_INTELLIGENCE_SCHEMA_VERSION,
        "actors": [{
            "name": "Core Actor",
            "simulation_tier": 1,
            "dimensions": dimensions,
        }],
    }
    return (
        "# Actor dossier\n\n### Actor: Core Actor\n\n"
        + "Substantive sourced actor history, incentives, plans, actions, "
        "investments, decisions, relationships, and constraints. " * 12
        + "\n\n<!-- ACTOR_INTELLIGENCE_LEDGER_V1\n"
        + json.dumps(ledger)
        + "\n-->\n"
    )


def test_evidence_only_baseline_can_own_shared_actor_track(mod, monkeypatch):
    """The orchestrator assigns Track B to exactly the baseline evidence lane."""
    monkeypatch.setenv("DEERFLOW_DUAL_TRACK", "true")
    assert mod._should_run_actor_track(evidence_only=True) is True
    assert mod._should_run_actor_track(evidence_only=False) is True

    monkeypatch.setenv("DEERFLOW_DUAL_TRACK", "false")
    assert mod._should_run_actor_track(evidence_only=True) is False
    assert mod._should_run_actor_track(evidence_only=False) is False


def test_evidence_only_main_publishes_track_a_with_shared_track_b(
        mod, tmp_path, monkeypatch):
    """Exercise the CLI wiring without a real provider call or hanging thread."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key-not-used")
    monkeypatch.setenv("DEERFLOW_DUAL_TRACK", "true")
    monkeypatch.setenv("RESEARCH_EVIDENCE_ONLY", "false")
    monkeypatch.setattr(
        mod,
        "runtime_skill_sync_telemetry",
        lambda: {
            "runtime_verified": False,
            "outcome": "test",
            "skills": {},
        },
    )

    fake_deerflow = ModuleType("deerflow")
    fake_client_module = ModuleType("deerflow.client")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

    fake_client_module.DeerFlowClient = FakeClient
    monkeypatch.setitem(sys.modules, "deerflow", fake_deerflow)
    monkeypatch.setitem(sys.modules, "deerflow.client", fake_client_module)

    calls = {"track_a": 0, "track_b": 0}
    search_receipts = _actor_gap_search_receipts(mod)

    def fake_track_a(*_args, **_kwargs):
        calls["track_a"] += 1
        return mod.render_evidence_pack(["Verified evidence. " * 60])

    def fake_track_b(*_args, **_kwargs):
        calls["track_b"] += 1
        return _valid_actor_dossier(mod, search_receipts)

    monkeypatch.setattr(mod, "run_research_stage", fake_track_a)
    monkeypatch.setattr(mod, "run_actor_ontology_stage", fake_track_b)
    monkeypatch.setattr(
        mod,
        "_track_b_search_result_receipts",
        lambda _thread_id="": [dict(row) for row in search_receipts],
    )
    monkeypatch.setattr(
        mod,
        "export_fetched_sources_for_manifest",
        lambda: [(
            lambda excerpt: {
                "url": "https://example.com/source",
                "title": "Verified source",
                "tier": "S1",
                "source_origin": "fetched",
                "reachable": True,
                "excerpt": excerpt,
                "content_sha256": hashlib.sha256(
                    excerpt.encode("utf-8")
                ).hexdigest(),
                "receipt_id": "receipt-track-b-1",
                "thread_id": "research-test",
                "lane": "track-b",
                "purpose": "actor-ontology",
                "receipt_scopes": [
                    {
                        "thread_id": "research-test",
                        "lane": "track-b",
                        "purpose": "actor-ontology",
                        "receipt_id": f"receipt-track-b-{attempt}",
                        "content_sha256": hashlib.sha256(
                            excerpt.encode("utf-8")
                        ).hexdigest(),
                    }
                    for attempt in (1, 2)
                ],
            }
        )("\n".join(
            f"Core Actor has grounded {name} evidence."
            for name in (
                "identity_history",
                "incentives",
                "capabilities",
                "current_actions",
                "decision_rights_process_triggers",
            )
        ))],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deerflow_research.py",
            "--prompt", "Forecast question",
            "--out-dir", str(tmp_path),
            "--model", "minimax",
            "--depth", "deep",
            "--evidence-only",
        ],
    )

    assert mod.main() == 0
    assert calls == {"track_a": 1, "track_b": 1}
    assert (tmp_path / mod.EVIDENCE_PACK_FILENAME).stat().st_size > 400
    meta = json.loads((tmp_path / mod.META_FILENAME).read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert meta["actor_dossier_generated"] is True
    assert meta["actor_dossier_required"] is True
    assert meta["actor_dossier_coverage"]["accountable"] is True


# ---------------------------------------------------------------------------
# RES-2: per-turn fetch accounting (id pairing, FIFO fallback, locked merge)
# ---------------------------------------------------------------------------

class TestFetchAccountingV2:
    def test_exact_tool_call_id_pairing_out_of_order(self, mod):
        pending = []
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://a.com/x"}, call_id="c1")
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://b.com/y"}, call_id="c2")
        # results return in completion order (c2 first) — dead result must hit c2, not c1
        mod._pending_mark_result(pending, "web_fetch", "No content could be extracted", call_id="c2")
        mod._pending_mark_result(pending, "web_fetch", LIVE_CONTENT, call_id="c1")
        by_id = {p["call_id"]: p["ok"] for p in pending}
        assert by_id == {"c1": True, "c2": False}

    def test_missing_id_refuses_ambiguous_parallel_pairing(self, mod):
        pending = []
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://a.com"})
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://b.com"})
        mod._pending_mark_result(pending, "web_fetch", LIVE_CONTENT)
        assert [row["ok"] for row in pending] == [None, None]

    def test_missing_id_pairs_only_single_unresolved_fetch(self, mod):
        pending = []
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://a.com"})
        mod._pending_mark_result(pending, "web_fetch", LIVE_CONTENT)
        assert pending[0]["ok"] is True

    def test_unknown_nonempty_id_never_falls_back_to_another_call(self, mod):
        pending = []
        mod._pending_record_fetch(
            pending, "web_fetch", {"url": "http://a.com"}, call_id="known"
        )
        mod._pending_mark_result(
            pending, "web_fetch", LIVE_CONTENT, call_id="unknown"
        )
        assert pending[0]["ok"] is None

    def test_repeat_fetch_keeps_alignment(self, mod):
        # the wedged run re-fetched the same URL in 4 passes; each repeat must own a slot
        pending = []
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://same.com/page"}, call_id="c1")
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://same.com/page"}, call_id="c2")
        mod._pending_record_fetch(pending, "web_fetch", {"url": "http://other.com"}, call_id="c3")
        mod._pending_mark_result(pending, "web_fetch", "Timeout was reached", call_id="c2")
        mod._pending_mark_result(pending, "web_fetch", LIVE_CONTENT, call_id="c1")
        mod._pending_mark_result(pending, "web_fetch", LIVE_CONTENT, call_id="c3")
        mod._merge_pending_fetches(pending)
        urls = {s["url"] for s in mod._FETCHED_SOURCES}
        assert urls == {"http://same.com/page", "http://other.com"}
        assert all(s["ok"] is True for s in mod._FETCHED_SOURCES)

    def test_merge_only_confirmed_rows(self, mod):
        pending = [
            {"url": "http://live.com", "call_id": "a", "ok": True},
            {"url": "http://dead.com", "call_id": "b", "ok": False},
            {"url": "http://pending.com", "call_id": "c", "ok": None},
        ]
        mod._merge_pending_fetches(pending)
        assert {s["url"] for s in mod._FETCHED_SOURCES} == {"http://live.com"}

    def test_merge_never_deletes_live_row(self, mod):
        # a dead re-fetch of an already-confirmed URL must not remove the live row
        mod._FETCHED_SOURCES.append({"url": "http://live.com", "ok": True})
        mod._merge_pending_fetches([{"url": "http://live.com", "call_id": "x", "ok": False}])
        assert mod._FETCHED_SOURCES == [{"url": "http://live.com", "ok": True}]

    def test_distinct_count_strict_in_v2(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_FETCH_ACCOUNTING_V2", raising=False)  # default on
        mod._FETCHED_SOURCES.extend([
            {"url": "http://a.com", "ok": True},
            {"url": "http://b.com", "ok": None},   # never resolved → not a confirmed read
        ])
        assert mod.distinct_fetched_count() == 1

    def test_distinct_count_legacy_flag_off(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_FETCH_ACCOUNTING_V2", "false")
        mod._FETCHED_SOURCES.extend([
            {"url": "http://a.com", "ok": True},
            {"url": "http://b.com", "ok": None},   # legacy predicate counted pending
        ])
        assert mod.distinct_fetched_count() == 2

    def test_run_streamed_turn_records_and_merges(self, mod, tmp_path, monkeypatch):
        monkeypatch.delenv("RESEARCH_FETCH_ACCOUNTING_V2", raising=False)

        events = [
            SimpleNamespace(type="messages-tuple", data={
                "type": "ai", "id": "m1", "content": "",
                "tool_calls": [{"name": "web_fetch", "args": {"url": "http://real.com/doc"}, "id": "t1"}],
            }),
            SimpleNamespace(type="messages-tuple", data={
                "type": "tool", "name": "web_fetch", "content": LIVE_CONTENT, "tool_call_id": "t1",
            }),
            SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m2", "content": "final text"}),
        ]
        client = SimpleNamespace(stream=lambda message, thread_id=None, recursion_limit=None: iter(events))
        plog = _plog(mod, tmp_path)
        try:
            text = mod.run_streamed_turn(client, "q", "tid", 10, plog, "test")
        finally:
            plog.close()
        assert text == "final text"
        assert mod.distinct_fetched_count() == 1
        assert mod._FETCHED_SOURCES[0]["url"] == "http://real.com/doc"


# ---------------------------------------------------------------------------
# RES-1: minimum gathered-context floor for the tool-free synthesis nets
# ---------------------------------------------------------------------------

class TestSynthesisContextFloor:
    def test_floor_default_and_override(self, mod, monkeypatch):
        monkeypatch.delenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", raising=False)
        assert mod._synth_min_context_chars() == 3000
        monkeypatch.setenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", "500")
        assert mod._synth_min_context_chars() == 500
        monkeypatch.setenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", "not-a-number")
        assert mod._synth_min_context_chars() == 3000

    def test_track_a_refuses_stub_context(self, mod, tmp_path, monkeypatch):
        # the wedged run: 47-char thread context → the bare model would fabricate
        monkeypatch.delenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", raising=False)
        client = SimpleNamespace(get_thread=lambda tid: {
            "checkpoints": [{"values": {"messages": [{"type": "ai", "content": "a 47-char reasoning stub with no research"}]}}],
        })
        plog = _plog(mod, tmp_path)
        try:
            out = mod.synthesize_from_thread(client, "tid", "q", None, "minimax", plog)
        finally:
            plog.close()
        assert out == ""
        assert any("refused tool-free fabrication" in f for f in mod._RESEARCH_FLAGS)

    def test_track_b_stage_short_circuits_without_judge(self, mod, tmp_path, monkeypatch):
        monkeypatch.delenv("ACTOR_SYNTH_MIN_CONTEXT_CHARS", raising=False)
        monkeypatch.delenv("ACTOR_DOSSIER_JUDGE", raising=False)

        stub_events = [SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "tiny stub"})]
        client = SimpleNamespace(
            stream=lambda message, thread_id=None, recursion_limit=None: iter(stub_events),
            get_thread=lambda tid: {"checkpoints": [{"values": {"messages": [{"type": "ai", "content": "tiny stub"}]}}]},
        )
        plog = _plog(mod, tmp_path)
        try:
            dossier = mod.run_actor_ontology_stage(
                client, "question", "standard", None, "minimax", "tid", plog, tmp_path)
        finally:
            plog.close()
        assert dossier == ""  # main() degrades cleanly to single-track on empty dossier
        assert not (tmp_path / "actor_dossier_judge.json").exists()  # no judge burn on ''
        log = (tmp_path / "progress.log").read_text(encoding="utf-8")
        assert "refusing tool-free fabrication" in log
        assert "skipping judge loop" in log


# ---------------------------------------------------------------------------
# RES-3: as-of reference-date clamp
# ---------------------------------------------------------------------------

class TestAsofClamp:
    RUN = dt.date(2026, 6, 30)

    def test_hallucinated_training_cutoff_is_overridden(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_ASOF_MAX_LAG_DAYS", raising=False)
        ref, override = mod._clamp_asof_reference(dt.date(2026, 1, 15), self.RUN)
        assert ref == self.RUN
        assert override == {"extracted": "2026-01-15", "used": "2026-06-30"}
        # the false "future-dated actual" flags disappear with the clamp:
        facts = [{"metric": "Moody's US downgrade", "as_of_date": "2026-04-18", "value": "1", "unit": "event"}]
        assert mod.flag_implausible_quant(facts, ref) == []

    def test_recent_extraction_is_kept(self, mod, monkeypatch):
        monkeypatch.delenv("RESEARCH_ASOF_MAX_LAG_DAYS", raising=False)
        extracted = self.RUN - dt.timedelta(days=10)
        assert mod._clamp_asof_reference(extracted, self.RUN) == (extracted, None)

    def test_missing_extraction_defaults_to_run_date(self, mod):
        assert mod._clamp_asof_reference(None, self.RUN) == (self.RUN, None)

    def test_lag_window_env_override(self, mod, monkeypatch):
        monkeypatch.setenv("RESEARCH_ASOF_MAX_LAG_DAYS", "200")
        extracted = self.RUN - dt.timedelta(days=166)
        assert mod._clamp_asof_reference(extracted, self.RUN) == (extracted, None)


# ---------------------------------------------------------------------------
# RES-4: grounding component + capped quant penalty in research_quality
# ---------------------------------------------------------------------------

class TestResearchQualityGrounding:
    SOURCES = [{"tier": "S1"}, {"tier": "S2"}]
    ACTORS = {"actors": [{"name": "A", "incentives": {"gains": "x"}, "values": ["v"],
                          "worldview": "w"}] * 10,
              "relationships": [{"source": "A", "target": "B"}] * 20}

    def test_defaults_match_old_formula(self, mod):
        old = mod.compute_research_quality(self.SOURCES, self.ACTORS)
        again = mod.compute_research_quality(self.SOURCES, self.ACTORS, None, grounding=None, quant_penalty=0.0)
        assert old["score"] == again["score"]
        assert old["components"].get("grounding") is None
        assert "quant_penalty" not in old

    def test_grounding_pulls_down_ungrounded_run(self, mod):
        judge = {"scores": {"a": 5, "b": 5}}
        base = mod.compute_research_quality(self.SOURCES, self.ACTORS, judge)
        scored = mod.compute_research_quality(self.SOURCES, self.ACTORS, judge, grounding=0.5)
        assert scored["score"] < base["score"]
        assert scored["components"]["grounding"] == 0.5

    def test_quant_penalty_is_capped(self, mod):
        base = mod.compute_research_quality(self.SOURCES, self.ACTORS)
        hit = mod.compute_research_quality(self.SOURCES, self.ACTORS, quant_penalty=0.9)
        assert hit["quant_penalty"] == 0.15
        assert base["score"] - hit["score"] == pytest.approx(0.15, abs=0.002)


# ---------------------------------------------------------------------------
# RES-8: dossier-level degraded-artifact guard
# ---------------------------------------------------------------------------

class TestDegradedArtifactGuard:
    def test_minimax_content_filter_message_is_degraded(self, mod):
        assert mod._is_degraded_artifact("Error code: 422 unprocessable_entity new_sensitive", 400)

    def test_short_stub_is_degraded_at_default_floor(self, mod):
        assert mod._is_degraded_artifact("a benign but useless 47-char stub", 400)

    def test_real_dossier_passes(self, mod):
        assert not mod._is_degraded_artifact("# Actor Dossier\n" + "Real researched content. " * 40, 400)

    def test_empty_is_not_flagged_here(self, mod):
        # the empty case keeps its own pre-existing branch in main()
        assert not mod._is_degraded_artifact("", 400)

    def test_floor_zero_keeps_sentinel_check_only(self, mod):
        assert not mod._is_degraded_artifact("short benign stub", 0)
        assert mod._is_degraded_artifact("unprocessable_entity", 0)


# ---------------------------------------------------------------------------
# RES-9: judge pass bar — default vs strict
# ---------------------------------------------------------------------------

class TestJudgePassBar:
    SCORECARD = {"verdict": "PASS", "scores": {
        "cast_correctness": 5, "salience_ranking": 4, "per_actor_depth": 4,
        "relationship_completeness": 4, "history_evolution": 4, "evidence_grounding": 4,
        "contradiction_handling": 3, "ontology_readiness": 5,
        "forward_behavior_coverage": 4, "cast_wide_accountability": 4,
    }}

    def test_default_bar_tolerates_one_noncritical_3(self, mod, monkeypatch):
        monkeypatch.delenv("ACTOR_DOSSIER_JUDGE_STRICT", raising=False)
        assert mod.dossier_passes(self.SCORECARD) is True

    def test_strict_bar_requires_all_dims_ge_4(self, mod, monkeypatch):
        monkeypatch.setenv("ACTOR_DOSSIER_JUDGE_STRICT", "true")
        assert mod.dossier_passes(self.SCORECARD) is False

    def test_skill_doc_states_implemented_bar(self, mod):
        skill = (REPO / "deerflow_bridge" / "skills" / "actor-ontology-research" / "SKILL.md").read_text(encoding="utf-8")
        assert "The **mean** across all ten dimensions is ≥ **4**" in skill
        assert "- **Every** dimension ≥ **4**, AND" not in skill

# ---------------------------------------------------------------------------
# RES-10: prediction_requirement.txt goes through the atomic writer
# ---------------------------------------------------------------------------

def test_requirement_file_written_atomically(mod):
    src = BRIDGE_PY.read_text(encoding="utf-8")
    assert "_atomic_write_text(out_dir / REQUIREMENT_FILENAME" in src
    assert "(out_dir / REQUIREMENT_FILENAME).write_text" not in src


# ---------------------------------------------------------------------------
# RES-11: completeness-probe concept screen + standard-depth coverage gate hook
# ---------------------------------------------------------------------------

class TestCoverageProbe:
    def test_abstract_concepts_are_screened(self, mod):
        assert mod._is_abstract_concept("Modern Mercantilism")
        assert mod._is_abstract_concept("Artificial Intelligence")
        assert not mod._is_abstract_concept("Bridgewater Associates")

    def test_coverage_gaps_skip_concepts_keep_real_entities(self, mod):
        obj = {
            "central_question": "Will Modern Mercantilism benefit Ray Dalio more than Bridgewater?",
            "actors": [{"name": "Bridgewater Associates"}],
        }
        gaps = mod.compute_coverage_gaps(obj)
        assert "Modern Mercantilism" not in gaps["missing_named_entities"]
        assert "Ray Dalio" in gaps["missing_named_entities"]

    def test_standard_depth_low_source_count_is_diagnostic_not_a_quota(
        self, mod, tmp_path, monkeypatch
    ):
        # LOOP-010: an empty source counter alone no longer forces two blind
        # broadening turns.  With no explicit KIQ gap, standard depth stops after
        # its opening report even though the breadth diagnostic is below reference.
        monkeypatch.delenv("RESEARCH_COVERAGE_GATE_STANDARD", raising=False)
        monkeypatch.delenv("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", raising=False)
        monkeypatch.delenv("RESEARCH_MIN_SOURCES", raising=False)
        calls = []

        def _stream(message, thread_id=None, recursion_limit=None):
            calls.append(message)
            return iter([SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m", "content": "report body"})])

        client = SimpleNamespace(stream=_stream)  # no get_thread → re-synthesis degrades safely
        plog = _plog(mod, tmp_path)
        try:
            out = mod.run_research_stage(client, "q", "standard", None, "claude", "tid", plog)
        finally:
            plog.close()
        assert out == "report body"
        assert len(calls) == 1
        assert "diagnostic only" in (tmp_path / "progress.log").read_text()

    def test_standard_depth_explicit_kiq_gap_gets_targeted_topup(
        self, mod, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("RESEARCH_COVERAGE_GATE_STANDARD", raising=False)
        monkeypatch.setenv("RESEARCH_COVERAGE_GATE_MAX_ROUNDS", "2")
        calls = []
        responses = iter([
            "# Report\n\n## Gaps to carry into the next pass\n- Verify the base rate",
            "## Evidence gathered\n- Base rate verified [S1]\n\n"
            "## Gaps to carry into the next pass\n",
        ])

        def _stream(message, thread_id=None, recursion_limit=None):
            calls.append(message)
            return iter([SimpleNamespace(
                type="messages-tuple",
                data={"type": "ai", "id": f"m{len(calls)}", "content": next(responses)},
            )])

        client = SimpleNamespace(stream=_stream)
        plog = _plog(mod, tmp_path)
        try:
            out = mod.run_research_stage(
                client, "q", "standard", None, "claude", "tid", plog
            )
        finally:
            plog.close()
        assert out.startswith("# Report")
        assert len(calls) == 2
        assert calls[1].startswith("/deep-research\nTARGETED GAP-CLOSING PASS")

    def test_standard_depth_gate_opt_out_restores_single_turn(self, mod, tmp_path, monkeypatch):
        # flag explicitly off: one turn, no top-up even with 0 fetched sources (old default)
        monkeypatch.setenv("RESEARCH_COVERAGE_GATE_STANDARD", "false")
        calls = []

        def _stream(message, thread_id=None, recursion_limit=None):
            calls.append(message)
            return iter([SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m", "content": "report body"})])

        client = SimpleNamespace(stream=_stream)
        plog = _plog(mod, tmp_path)
        try:
            out = mod.run_research_stage(client, "q", "standard", None, "claude", "tid", plog)
        finally:
            plog.close()
        assert out == "report body"
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# RES-7: bridge config.yaml back-ported from the deployed deer-flow config
# ---------------------------------------------------------------------------

class TestConfigParity:
    def _load(self, path):
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8").replace("$", ""))

    def test_claude_stanza_no_double_nested_thinking(self):
        cfg = self._load(BRIDGE_CFG)
        claude = next(m for m in cfg["models"] if m["name"] == "claude")
        # the when_thinking_enabled:{thinking:{type:enabled}} block double-nested into the
        # request payload (thinking.thinking) → 8/8 Anthropic rejects = empty research
        assert "when_thinking_enabled" not in claude
        assert claude.get("supports_thinking") is False

    def test_antigravity_stanza_present(self):
        cfg = self._load(BRIDGE_CFG)
        assert any(m["name"] == "antigravity" for m in cfg["models"])

    @pytest.mark.skipif(not DEPLOYED_CFG.exists(), reason="deployed deer-flow checkout absent")
    def test_bridge_matches_deployed(self):
        assert BRIDGE_CFG.read_text(encoding="utf-8") == DEPLOYED_CFG.read_text(encoding="utf-8")
