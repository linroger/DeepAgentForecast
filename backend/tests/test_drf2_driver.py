"""Offline tests for the DRF-2 Pipeline Driver (drf2/driver/*).

Covers: stage state-machine transitions + schema versioning, resume with manifest
hash-verified reuse, every ported health gate against fixture artifacts, ensemble
log-odds pooling math, poll watchdogs, and a full dry-run pipeline with the fake
harness + fake sim engine. No network / subprocess / LLM.
"""

import json
import math
import os
import sys

import pytest

# drf2 位于仓库根（backend/ 的上一级）；conftest 只加了 backend/。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from drf2.driver.state import (  # noqa: E402
    SCHEMA_VERSION, STAGES, DriverState, IncompatibleStateSchema, InvalidTransition, StateStore,
)
from drf2.driver.manifest import ArtifactManifest, sha256_file  # noqa: E402
from drf2.driver import gates  # noqa: E402
from drf2.driver.ensemble import aggregate_ensemble, fan_out  # noqa: E402
from drf2.driver.harness_client import (  # noqa: E402
    DryRunHarness, DrySimEngine, RunsApiHarness, poll_simulation,
)
from drf2.driver.pipeline import PipelineDriver, STAGE_SKILLS  # noqa: E402
from drf2.driver import cli as drf2_cli  # noqa: E402


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_new_state_has_all_stages_pending(self):
        st = DriverState.new("q")
        assert st.schema_version == SCHEMA_VERSION
        assert tuple(st.stages) == STAGES
        assert all(s.status == "pending" for s in st.stages.values())
        assert st.thread_id == f"drf2-{st.pipeline_id}"

    def test_legal_transition_cycle(self):
        st = DriverState.new("q")
        st.start_stage("research")
        assert st.stages["research"].status == "running"
        assert st.status == "running" and st.current_stage == "research"
        st.complete_stage("research", health="degraded", issues=["x"])
        assert st.stages["research"].status == "completed"
        # completed → running 允许（manifest 校验失败强制重建）
        st.start_stage("research")
        st.fail_stage("research", "boom")
        assert st.status == "failed" and "boom" in st.error
        st.start_stage("research")  # failed → running（重试）

    def test_illegal_transitions_raise(self):
        st = DriverState.new("q")
        with pytest.raises(InvalidTransition):
            st.complete_stage("research")  # pending → completed 不允许
        with pytest.raises(InvalidTransition):
            st.mark_stage_reused("research")  # 只能 reuse completed 阶段

    def test_save_load_roundtrip(self, tmp_path):
        store = StateStore(str(tmp_path))
        st = DriverState.new("q", pipeline_id="p1")
        st.start_stage("research")
        st.complete_stage("research", artifacts={"research:research_report.md": "/x"})
        store.save(st)
        loaded = store.load("p1")
        assert loaded.pipeline_id == "p1"
        assert loaded.stages["research"].status == "completed"
        assert loaded.stages["research"].artifacts == {"research:research_report.md": "/x"}

    def test_newer_schema_refused(self, tmp_path):
        store = StateStore(str(tmp_path))
        st = DriverState.new("q", pipeline_id="p2")
        store.save(st)
        data = json.load(open(store.state_path("p2"), encoding="utf-8"))
        data["schema_version"] = SCHEMA_VERSION + 1
        json.dump(data, open(store.state_path("p2"), "w", encoding="utf-8"))
        with pytest.raises(IncompatibleStateSchema):
            store.load("p2")

    def test_invalid_pipeline_id_rejected(self, tmp_path):
        store = StateStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.state_path("../escape")


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

class TestManifest:
    def _mk(self, tmp_path, content="hello world"):
        art = tmp_path / "a.md"
        art.write_text(content, encoding="utf-8")
        m = ArtifactManifest(str(tmp_path / "manifest.json"))
        m.record_stage("research", {"research:a.md": str(art)})
        return m, art

    def test_record_and_verify_pass(self, tmp_path):
        m, art = self._mk(tmp_path)
        m2 = ArtifactManifest(str(tmp_path / "manifest.json"))  # 从磁盘重载
        ok, problems = m2.verify_stage("research", {"research:a.md": str(art)})
        assert ok and not problems
        assert m2.entries["research:a.md"]["sha256"] == sha256_file(str(art))

    def test_content_tamper_same_bytes_fails(self, tmp_path):
        m, art = self._mk(tmp_path, "hello world")
        art.write_text("hello w0rld", encoding="utf-8")  # 同字节数、不同内容
        ok, problems = m.verify_stage("research", {"research:a.md": str(art)})
        assert not ok and "sha256 mismatch" in problems[0]

    def test_byte_change_fails(self, tmp_path):
        m, art = self._mk(tmp_path)
        art.write_text("longer content now", encoding="utf-8")
        ok, problems = m.verify_stage("research", {"research:a.md": str(art)})
        assert not ok and "bytes changed" in problems[0]

    def test_missing_file_fails(self, tmp_path):
        m, art = self._mk(tmp_path)
        art.unlink()
        ok, problems = m.verify_stage("research", {"research:a.md": str(art)})
        assert not ok and "file missing" in problems[0]

    def test_unregistered_artifact_skipped(self, tmp_path):
        m, art = self._mk(tmp_path)
        ok, _ = m.verify_stage("research", {"research:a.md": str(art),
                                            "research:optional.json": str(tmp_path / "nope.json")})
        assert ok  # 未登记的可选产物不校验

    def test_empty_manifest_degrades_to_existence_reuse(self, tmp_path):
        m = ArtifactManifest(str(tmp_path / "manifest.json"))
        ok, _ = m.verify_stage("research", {"research:a.md": str(tmp_path / "gone.md")})
        assert ok  # 无清单 → 放行（向后兼容决策）

    def test_stage_scoped_entries_also_verified(self, tmp_path):
        # ensemble 场景：登记了 seed 产物但默认 specs 不含它 → 仍应校验
        m, art = self._mk(tmp_path)
        seed = tmp_path / "seed-1" / "run_summary.json"
        seed.parent.mkdir()
        seed.write_text('{"ok": true}', encoding="utf-8")
        m.record_stage("run", {"run:seed-1/run_summary.json": str(seed)})
        seed.unlink()
        ok, problems = m.verify_stage("run", {})
        assert not ok and "file missing" in problems[0]

    def test_json_probe(self, tmp_path):
        bad = tmp_path / "b.json"
        bad.write_text("{not json", encoding="utf-8")
        m = ArtifactManifest(str(tmp_path / "manifest.json"))
        assert m.record("run:b.json", str(bad), "run")["schema_ok"] is False


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

class TestResearchGate:
    def test_good_report_passes(self, tmp_path):
        (tmp_path / "research_report.md").write_text("evidence\n" * 100, encoding="utf-8")
        g = gates.research_gate(str(tmp_path))
        assert g.status == "pass" and not g.issues

    def test_missing_report_fails(self, tmp_path):
        assert gates.research_gate(str(tmp_path)).status == "fail"

    def test_short_report_fails_floor(self, tmp_path):
        (tmp_path / "research_report.md").write_text("too short", encoding="utf-8")
        g = gates.research_gate(str(tmp_path), min_report_chars=400)
        assert g.status == "fail" and "< floor 400" in g.issues[0]

    def test_llm_error_marker_fails(self, tmp_path):
        (tmp_path / "research_report.md").write_text(
            "The configured LLM provider is temporarily unavailable.", encoding="utf-8")
        g = gates.research_gate(str(tmp_path), min_report_chars=0)
        assert g.status == "fail" and "LLM degradation" in g.issues[0]

    def test_degraded_dossier_flagged(self, tmp_path):
        (tmp_path / "research_report.md").write_text("evidence\n" * 100, encoding="utf-8")
        (tmp_path / "actor_dossier.md").write_text("LLM request failed: 500", encoding="utf-8")
        g = gates.research_gate(str(tmp_path))
        assert g.status == "degraded" and "degraded artifact" in g.issues[0]

    def test_quality_floor_soft_warns(self, tmp_path):
        (tmp_path / "research_report.md").write_text("evidence\n" * 100, encoding="utf-8")
        (tmp_path / "meta.json").write_text(
            json.dumps({"research_quality": {"score": 0.3}}), encoding="utf-8")
        g = gates.research_gate(str(tmp_path), quality_floor=0.6)
        assert g.status == "degraded"  # 软告警：继续，不硬失败（I-0-3 决策）
        assert "0.3" in g.issues[0] and g.meta["research_quality_score"] == 0.3


class TestHollowSimGate:
    def test_healthy_sim_passes(self, tmp_path):
        (tmp_path / "run_summary.json").write_text(
            json.dumps({"organic_action_count": 37, "simulation_health": "ok"}), encoding="utf-8")
        g = gates.hollow_sim_gate(str(tmp_path))
        assert g.status == "pass" and g.meta["organic_actions"] == 37

    def test_hollow_sim_degraded_never_fail(self, tmp_path):
        (tmp_path / "run_summary.json").write_text(
            json.dumps({"organic_action_count": 0, "simulation_health": "hollow"}), encoding="utf-8")
        g = gates.hollow_sim_gate(str(tmp_path))
        assert g.status == "degraded"  # 决策：空心 = degraded，绝不 hard-fail
        assert any("0 organic" in i for i in g.issues)
        assert any("simulation_health=hollow" in i for i in g.issues)

    def test_truncated_and_errored(self, tmp_path):
        (tmp_path / "run_summary.json").write_text(
            json.dumps({"organic_action_count": 5}), encoding="utf-8")
        (tmp_path / "run_state.json").write_text(
            json.dumps({"error": "provider exhausted", "current_round": 2, "total_rounds": 6}),
            encoding="utf-8")
        g = gates.hollow_sim_gate(str(tmp_path))
        assert g.status == "degraded"
        assert any("truncated" in i for i in g.issues)
        assert any("provider exhausted" in i for i in g.issues)

    def test_missing_summary_degraded(self, tmp_path):
        g = gates.hollow_sim_gate(str(tmp_path))
        assert g.status == "degraded" and "unverifiable" in g.issues[0]


def _binaries(probs, sharp=True):
    return [{"statement": f"s{i}", "probability": p, "criteria_sharp": sharp}
            for i, p in enumerate(probs)]


class TestBinaryConvictionGate:
    def test_committed_spread_passes(self, tmp_path):
        p = tmp_path / "forecast.json"
        probs = [0.85, 0.8, 0.75, 0.72, 0.25, 0.2, 0.15, 0.55, 0.62, 0.35]
        p.write_text(json.dumps({"binary_forecasts": _binaries(probs)}), encoding="utf-8")
        g = gates.binary_conviction_gate(str(p))
        assert g.status == "pass"
        assert g.meta["count"] == 10 and g.meta["prob_stdev"] >= 0.12

    def test_hedged_wall_of_coinflips_degraded(self, tmp_path):
        p = tmp_path / "forecast.json"
        p.write_text(json.dumps({"binary_forecasts": _binaries([0.5] * 12)}), encoding="utf-8")
        g = gates.binary_conviction_gate(str(p))
        assert g.status == "degraded"
        assert any("spread too low" in i for i in g.issues)

    def test_too_few_binaries_degraded(self, tmp_path):
        p = tmp_path / "forecast.json"
        p.write_text(json.dumps({"binary_forecasts": _binaries([0.9, 0.1, 0.8])}), encoding="utf-8")
        g = gates.binary_conviction_gate(str(p), min_count=10)
        assert g.status == "degraded" and any("only 3 binaries" in i for i in g.issues)

    def test_vague_criteria_degraded(self, tmp_path):
        p = tmp_path / "forecast.json"
        probs = [0.85, 0.8, 0.75, 0.72, 0.25, 0.2, 0.15, 0.55, 0.62, 0.35]
        p.write_text(json.dumps({"binary_forecasts": _binaries(probs, sharp=False)}), encoding="utf-8")
        g = gates.binary_conviction_gate(str(p))
        assert g.status == "degraded" and any("objective metric" in i for i in g.issues)

    def test_missing_forecast_degraded(self, tmp_path):
        g = gates.binary_conviction_gate(str(tmp_path / "nope.json"))
        assert g.status == "degraded"

    def test_invalid_probabilities_dropped(self, tmp_path):
        p = tmp_path / "forecast.json"
        p.write_text(json.dumps({"binary_forecasts": [
            {"probability": "NaN"}, {"probability": 1.7}, {"probability": None}]}), encoding="utf-8")
        g = gates.binary_conviction_gate(str(p))
        assert g.status == "degraded" and g.meta.get("count") == 0


class TestDeliverableGate:
    def _good(self, tmp_path):
        (tmp_path / "full_report.md").write_text("analysis line\n" * 300, encoding="utf-8")
        (tmp_path / "forecast.json").write_text(
            json.dumps({"scenarios": [{"name": "A", "probability": 1.0}]}), encoding="utf-8")

    def test_good_deliverable_passes(self, tmp_path):
        self._good(tmp_path)
        (tmp_path / "section_01.md").write_text("substantive content " * 30, encoding="utf-8")
        assert gates.deliverable_gate(str(tmp_path)).status == "pass"

    def test_all_placeholder_sections_hard_fail(self, tmp_path):
        self._good(tmp_path)
        (tmp_path / "section_01.md").write_text("本章节生成失败", encoding="utf-8")
        (tmp_path / "section_02.md").write_text("生成失败", encoding="utf-8")
        g = gates.deliverable_gate(str(tmp_path))
        assert g.status == "fail" and "all 2 report sections" in g.issues[0]

    def test_partial_placeholder_degraded(self, tmp_path):
        self._good(tmp_path)
        (tmp_path / "section_01.md").write_text("substantive content " * 30, encoding="utf-8")
        (tmp_path / "section_02.md").write_text("生成失败", encoding="utf-8")
        g = gates.deliverable_gate(str(tmp_path))
        assert g.status == "degraded" and "1/2" in g.issues[0]

    def test_missing_forecast_hard_fail(self, tmp_path):
        (tmp_path / "full_report.md").write_text("analysis line\n" * 300, encoding="utf-8")
        g = gates.deliverable_gate(str(tmp_path))
        assert g.status == "fail" and any("forecast.json missing" in i for i in g.issues)

    def test_tiny_report_hard_fail(self, tmp_path):
        (tmp_path / "full_report.md").write_text("tiny", encoding="utf-8")
        (tmp_path / "forecast.json").write_text(
            json.dumps({"scenarios": [{"name": "A"}]}), encoding="utf-8")
        g = gates.deliverable_gate(str(tmp_path))
        assert g.status == "fail" and any("effectively empty" in i for i in g.issues)


# ---------------------------------------------------------------------------
# ensemble math
# ---------------------------------------------------------------------------

def _forecast(pa, pb):
    return {"headline": "h", "horizon": "2030", "scenarios": [
        {"name": "Alpha wins", "probability": pa, "key_drivers": ["k1"],
         "resolution_criteria": "X>1"},
        {"name": "Beta wins", "probability": pb, "key_drivers": ["k2"],
         "resolution_criteria": "X<=1"},
    ]}


class TestEnsembleMath:
    def test_arithmetic_mean_pooling(self):
        out = aggregate_ensemble([_forecast(0.6, 0.4), _forecast(0.8, 0.2)])
        assert out["n_runs"] == 2
        top = out["scenarios"][0]
        assert top["name"] == "Alpha wins"
        assert top["mean_probability"] == pytest.approx(0.7)
        assert top["probability"] == pytest.approx(0.7, abs=1e-4)  # 均值路径：归一化不变
        # 输出按 4 位小数取整（与 legacy schema 一致）
        assert top["stdev"] == pytest.approx(math.sqrt(((0.6 - 0.7) ** 2 + (0.8 - 0.7) ** 2) / 1), abs=1e-4)
        assert top["p_low"] == pytest.approx(top["probability"] - top["stdev"], abs=1e-3)
        assert top["pooling"] == "arithmetic_mean"
        assert out["agreement"] is not None and 0.0 <= out["agreement"] <= 1.0

    def test_extremized_logodds_pooling(self):
        out = aggregate_ensemble([_forecast(0.6, 0.4), _forecast(0.8, 0.2)], extremize_a=1.0)
        # pooled = sigmoid(mean(logit(0.6), logit(0.8)))，对称情景归一化后不变
        z = (math.log(0.6 / 0.4) + math.log(0.8 / 0.2)) / 2
        expected_a = 1.0 / (1.0 + math.exp(-z))
        pooled = {s["name"]: s for s in out["scenarios"]}
        total = expected_a + (1.0 - expected_a)
        assert pooled["Alpha wins"]["probability"] == pytest.approx(expected_a / total, abs=1e-3)
        assert pooled["Alpha wins"]["pooling"] == "extremized_logodds"
        assert out["extremize_a"] == 1.0
        # 极化把 0.7 均值推向更自信的 ~0.71+
        assert pooled["Alpha wins"]["probability"] > 0.7

    def test_scenario_matching_is_name_normalized(self):
        f2 = _forecast(0.8, 0.2)
        f2["scenarios"][0]["name"] = "  ALPHA   wins. "
        out = aggregate_ensemble([_forecast(0.6, 0.4), f2])
        assert len(out["scenarios"]) == 2  # 不因大小写/空白分裂成 3 桶
        assert out["scenarios"][0]["support"] == 2

    def test_empty_input(self):
        out = aggregate_ensemble([])
        assert out["n_runs"] == 0 and out["scenarios"] == [] and out["agreement"] is None

    def test_fan_out_isolates_seed_failures(self):
        def run_one(seed):
            if seed == 2:
                raise RuntimeError("seed 2 exploded")
            return _forecast(0.6, 0.4)
        out = fan_out([1, 2, 3], run_one)
        assert out["n_ok"] == 2
        assert out["results"]["2"]["ok"] is False and "exploded" in out["results"]["2"]["error"]
        assert out["aggregate"]["n_runs"] == 2


# ---------------------------------------------------------------------------
# harness / sim-engine clients
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 1.0)


class TestPollSimulation:
    def test_completes(self):
        eng = DrySimEngine(statuses=[{"completed": False, "current_round": 1},
                                     {"completed": True, "current_round": 3}])
        clk = _FakeClock()
        final = poll_simulation(eng, "s", timeout_s=1000, stall_s=0, interval_s=30,
                                clock=clk, sleep=clk.sleep)
        assert final["completed"] is True

    def test_stall_watchdog_stops_wedged_sim(self):
        eng = DrySimEngine(statuses=[{"completed": False, "current_round": 2}])  # 永不推进
        clk = _FakeClock()
        final = poll_simulation(eng, "s", timeout_s=100000, stall_s=100, interval_s=30,
                                clock=clk, sleep=clk.sleep)
        assert final["completed"] is False
        assert "stall watchdog" in final["error"] and "round 2" in final["error"]
        assert eng.stopped == ["s"]

    def test_driver_side_timeout(self):
        # current_round 每次都变 → 永不 stall，但整体超时必须兜底
        rounds = iter(range(10000))

        class Advancing(DrySimEngine):
            def sim_status(self, sim_id):
                return {"completed": False, "current_round": next(rounds)}
        eng = Advancing()
        clk = _FakeClock()
        final = poll_simulation(eng, "s", timeout_s=90, stall_s=1000, interval_s=30,
                                clock=clk, sleep=clk.sleep)
        assert final["completed"] is False and "timeout" in final["error"]
        assert eng.stopped == ["s"]


class TestRunsApiHarness:
    def _scripted(self, responses):
        calls = []

        def fake_request(method, path, body=None):
            calls.append((method, path, body))
            return responses.pop(0)
        h = RunsApiHarness("http://gw:8001", "t1", workspace_root="/ws", sleep=lambda s: None)
        h._request = fake_request
        return h, calls

    def test_run_stage_success(self):
        h, calls = self._scripted([
            {"run_id": "r1", "status": "pending"},   # POST runs
            {"run_id": "r1", "status": "running"},   # GET poll 1
            {"run_id": "r1", "status": "success"},   # GET poll 2
        ])
        res = h.run_stage("research", "/deep-research q", timeout_s=999)
        assert res.ok and res.run_id == "r1"
        assert calls[0][0] == "POST" and calls[0][1] == "/api/threads/t1/runs"
        assert calls[0][2]["input"]["messages"][0]["content"].startswith("/deep-research")
        assert calls[1][0] == "GET"

    def test_run_error_status_propagates(self):
        h, _ = self._scripted([{"run_id": "r2", "status": "pending"},
                               {"run_id": "r2", "status": "error"}])
        res = h.run_stage("research", "p", timeout_s=999)
        assert not res.ok and res.status == "error"

    def test_artifact_path_contract(self):
        h, _ = self._scripted([])
        p = str(h.artifact_path("forecast.json"))
        assert p == "/ws/users/default/threads/t1/user-data/outputs/forecast.json"


# ---------------------------------------------------------------------------
# full dry-run pipeline
# ---------------------------------------------------------------------------

def _driver(tmp_path, *, effects=None, statuses=None, sim=None, extremize_a=None):
    store = StateStore(str(tmp_path / "pipelines"))
    harness = DryRunHarness(workspace=tmp_path / "ws",
                            stage_effects=effects if effects is not None else drf2_cli._dry_effects(),
                            stage_statuses=statuses or {})
    engine = sim if sim is not None else drf2_cli._dry_sim_engine()
    return PipelineDriver(store, harness, engine,
                          sim_config={"interval_s": 0, "timeout_s": 60, "stall_s": 0},
                          extremize_a=extremize_a), store, harness


class TestDryRunPipeline:
    def test_full_pipeline_completes(self, tmp_path):
        driver, store, harness = _driver(tmp_path)
        state = driver.run("Will X happen?", pipeline_id="pipe1")
        assert state.status == "completed"
        assert all(s.status == "completed" for s in state.stages.values())
        # 每个知识阶段恰好一次 harness run，run 阶段不经 harness
        assert [s for s, _ in harness.prompts] == ["research", "ontology", "graph", "prepare", "report"]
        for stage, prompt in harness.prompts:
            assert prompt.startswith(f"/{STAGE_SKILLS[stage]} "), "严格斜杠技能激活语法"
        # 状态/清单落盘
        assert os.path.exists(store.state_path("pipe1"))
        m = ArtifactManifest(store.manifest_path("pipe1"))
        assert "research:research_report.md" in m.entries
        assert "run:run_summary.json" in m.entries
        # 模拟经引擎而非 harness 启动
        assert len(driver.sim_engine.started) == 1

    def test_harness_run_failure_fails_stage(self, tmp_path):
        driver, store, _ = _driver(tmp_path, statuses={"ontology": "error"})
        state = driver.run("q", pipeline_id="pipe2")
        assert state.status == "failed"
        assert state.stages["ontology"].status == "failed"
        assert state.stages["graph"].status == "pending"  # 失败即停，不越过

    def test_missing_required_artifact_fails_stage(self, tmp_path):
        effects = drf2_cli._dry_effects()
        effects.pop("prepare")  # prepare 什么都不写
        driver, _, _ = _driver(tmp_path, effects=effects)
        state = driver.run("q", pipeline_id="pipe3")
        assert state.status == "failed"
        assert "simulation_config.json" in state.stages["prepare"].error

    def test_deliverable_gate_hard_fails_report(self, tmp_path):
        effects = drf2_cli._dry_effects()

        def bad_report(ws, prompt):
            (ws / "full_report.md").write_text("tiny", encoding="utf-8")
            (ws / "forecast.json").write_text("{}", encoding="utf-8")
        effects["report"] = bad_report
        driver, _, _ = _driver(tmp_path, effects=effects)
        state = driver.run("q", pipeline_id="pipe4")
        assert state.status == "failed"
        assert "gate failed" in state.stages["report"].error

    def test_hollow_sim_degrades_but_continues(self, tmp_path):
        sim = DrySimEngine(statuses=[{
            "completed": True,
            "run_summary": {"organic_action_count": 0, "simulation_health": "hollow"}}])
        driver, _, _ = _driver(tmp_path, sim=sim)
        state = driver.run("q", pipeline_id="pipe5")
        assert state.status == "completed"  # 决策：空心 = degraded 而非硬失败
        assert state.stages["run"].health == "degraded"
        assert any("hollow" in i for i in state.stages["run"].issues)

    def test_resume_reuses_verified_stages(self, tmp_path):
        effects = drf2_cli._dry_effects()
        research_calls = {"n": 0}
        orig = effects["research"]

        def counting_research(ws, prompt):
            research_calls["n"] += 1
            orig(ws, prompt)
        effects["research"] = counting_research

        def bad_report(ws, prompt):
            (ws / "full_report.md").write_text("tiny", encoding="utf-8")
        effects["report"] = bad_report
        driver, store, harness = _driver(tmp_path, effects=effects)
        state = driver.run("q", pipeline_id="pipe6")
        assert state.status == "failed" and research_calls["n"] == 1

        # 修好 report 后 resume：前五阶段经 manifest 校验复用，只重跑 report
        effects["report"] = drf2_cli._dry_effects()["report"]
        state2 = driver.resume("pipe6")
        assert state2.status == "completed"
        assert research_calls["n"] == 1  # research 未重跑
        assert state2.stages["research"].reused is True
        assert state2.stages["run"].reused is True
        assert state2.stages["report"].reused is False

    def test_resume_rebuilds_on_manifest_mismatch(self, tmp_path):
        effects = drf2_cli._dry_effects()
        research_calls = {"n": 0}
        orig_research = effects["research"]

        def counting_research(ws, prompt):
            research_calls["n"] += 1
            orig_research(ws, prompt)
        effects["research"] = counting_research

        def bad_report(ws, prompt):
            (ws / "full_report.md").write_text("tiny", encoding="utf-8")
        good_report = effects["report"]
        effects["report"] = bad_report
        driver, store, harness = _driver(tmp_path, effects=effects)
        driver.run("q", pipeline_id="pipe7")
        # 篡改 research 产物（同长度不同内容 → sha 不符）
        rp = harness.workspace / "research_report.md"
        body = rp.read_text(encoding="utf-8")
        rp.write_text(body[:-1] + ("X" if body[-1] != "X" else "Y"), encoding="utf-8")
        effects["report"] = good_report
        state2 = driver.resume("pipe7")
        assert state2.status == "completed"
        assert research_calls["n"] == 2  # 哈希不符 → research 强制重建
        assert state2.stages["research"].reused is False
        assert state2.stages["ontology"].reused is True

    def test_ensemble_fan_out_and_pooling(self, tmp_path):
        driver, store, harness = _driver(tmp_path, extremize_a=1.0)
        state = driver.run("q", pipeline_id="pipe8", seeds=[7, 8, 9])
        assert state.status == "completed"
        ens = state.options["ensemble"]
        assert ens["n_ok"] == 3 and ens["seeds"] == [7, 8, 9]
        # 每种子一次模拟（带种子）+ 一次报告 run
        assert [s for _, s in driver.sim_engine.started] == [7, 8, 9]
        assert [s for s, _ in harness.prompts].count("report") == 3
        agg = json.load(open(harness.workspace / "ensemble_forecast.json", encoding="utf-8"))
        assert agg["n_runs"] == 3 and agg["extremize_a"] == 1.0
        assert (harness.workspace / "seed-7" / "run_summary.json").exists()
        m = ArtifactManifest(store.manifest_path("pipe8"))
        assert "report:ensemble_forecast.json" in m.entries
        assert "run:seed-8/run_summary.json" in m.entries

    def test_ensemble_partial_seed_failure_degrades(self, tmp_path):
        effects = drf2_cli._dry_effects()
        orig_report = effects["report"]

        def flaky_report(ws, prompt):
            if "seed-8" in prompt:
                raise RuntimeError("seed 8 report exploded")
            orig_report(ws, prompt)
        effects["report"] = flaky_report
        driver, _, harness = _driver(tmp_path, effects=effects)
        state = driver.run("q", pipeline_id="pipe9", seeds=[7, 8, 9])
        assert state.status == "completed"
        assert state.options["ensemble"]["n_ok"] == 2
        assert state.options["ensemble"]["results"]["8"]["ok"] is False
        assert state.stages["run"].health == "degraded"
        agg = json.load(open(harness.workspace / "ensemble_forecast.json", encoding="utf-8"))
        assert agg["n_runs"] == 2

    def test_ensemble_all_seeds_failed_fails_pipeline(self, tmp_path):
        effects = drf2_cli._dry_effects()

        def boom(ws, prompt):
            raise RuntimeError("no report today")
        effects["report"] = boom
        driver, _, _ = _driver(tmp_path, effects=effects)
        state = driver.run("q", pipeline_id="pipe10", seeds=[1, 2])
        assert state.status == "failed"
        assert state.stages["run"].status == "failed"
        assert state.stages["report"].status == "failed"


class TestCli:
    def test_cli_dry_run_and_status(self, tmp_path, capsys):
        base = str(tmp_path / "pipelines")
        rc = drf2_cli.main(["--config", str(tmp_path / "none.yaml"), "--base-dir", base,
                            "run", "--question", "Q?", "--dry-run", "--pipeline-id", "cli1"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "completed"
        rc = drf2_cli.main(["--base-dir", base, "status", "--pipeline-id", "cli1"])
        assert rc == 0
        st = json.loads(capsys.readouterr().out)
        assert st["schema_version"] == SCHEMA_VERSION

    def test_cli_unknown_pipeline_is_usage_error(self, tmp_path, capsys):
        rc = drf2_cli.main(["--base-dir", str(tmp_path), "status", "--pipeline-id", "nope"])
        assert rc == 2

    def test_cli_bad_seeds_rejected(self, tmp_path):
        rc = drf2_cli.main(["--base-dir", str(tmp_path), "run", "--question", "q",
                            "--seeds", "1,x", "--dry-run"])
        assert rc == 2

    def test_config_section_parsed(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("driver:\n  base_dir: /tmp/x\n  gates:\n    binary_min_count: 12\n",
                       encoding="utf-8")
        d = drf2_cli._load_driver_config(str(cfg))
        assert d["base_dir"] == "/tmp/x" and d["gates"]["binary_min_count"] == 12
