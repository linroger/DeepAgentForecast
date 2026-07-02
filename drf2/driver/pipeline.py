"""Pipeline core: sequence research→ontology→graph→prepare→run→report.

Stage execution model (REDESIGN §2 + brief):
  * knowledge stages = ONE harness run each, whose prompt names the skill (strict
    ``/skill-name task`` slash-activation syntax, see deerflow SkillActivationMiddleware)
    and the exact artifact files expected under /mnt/user-data/outputs;
  * every stage is followed by DETERMINISTIC checks — required-artifact presence,
    manifest hashing, and the ported health gates — reading artifacts directly,
    never via LLM;
  * the RUN stage is NOT a harness run: the driver calls the simulation engine's
    sim_start and polls driver-side with a stall watchdog;
  * resume skips a completed stage only when its manifest hashes re-verify;
  * multi-seed ensemble: run+report fan out per seed, forecasts pooled via
    log-odds ensemble math into ensemble_forecast.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from . import gates as _gates
from .ensemble import fan_out
from .harness_client import Harness, SimEngine, poll_simulation
from .legacy import ensure_backend_importable
from .manifest import ArtifactManifest
from .state import STAGES, DriverState, StateStore

ensure_backend_importable()
from app.utils.atomic import write_json_atomic  # noqa: E402

logger = logging.getLogger(__name__)

# 阶段 → 2.0 技能名（REDESIGN §2 的 7 技能中管线直接驱动的 5 个）。
STAGE_SKILLS: dict[str, str] = {
    "research": "deep-research",
    "ontology": "ontology-generation",
    "graph": "kg-construction",
    "prepare": "simulation-design",
    "report": "forecast-report",
}

# 阶段产物契约：required 缺失 = 阶段失败；optional 存在则登记进 manifest。
STAGE_ARTIFACTS: dict[str, dict[str, list[str]]] = {
    "research": {"required": ["research_report.md"],
                 "optional": ["actor_dossier.md", "actors.json", "sources.json",
                              "timeline.json", "meta.json"]},
    "ontology": {"required": ["ontology.json"], "optional": ["actors.json"]},
    "graph": {"required": ["kg_build_summary.json"], "optional": []},
    "prepare": {"required": ["simulation_config.json"], "optional": ["agent_profiles.json"]},
    "run": {"required": ["run_summary.json"], "optional": []},
    "report": {"required": ["full_report.md", "forecast.json"], "optional": []},
}

DEFAULT_STAGE_TIMEOUTS_S: dict[str, float] = {
    "research": 3600, "ontology": 1800, "graph": 3600, "prepare": 1800, "report": 3600,
}
DEFAULT_GATE_CONFIG: dict[str, Any] = {
    "min_report_chars": 400,          # RESEARCH_MIN_REPORT_CHARS 决策
    "research_quality_floor": 0.0,    # RESEARCH_QUALITY_FLOOR（0 = 关闭软告警）
    "binary_min_count": 10,           # 二元预测最少条数
    "min_report_bytes": 2000,         # full_report.md 硬下限
    "structured_forecast_required": True,
}
DEFAULT_SIM_CONFIG: dict[str, float] = {
    "timeout_s": 4 * 3600, "stall_s": 1800, "interval_s": 30,
}


def build_stage_prompt(stage: str, question: str, out_subdir: str = "") -> str:
    """严格斜杠语法激活技能 + 明示产物契约（文件名/路径由 driver 决定，模型只照写）。"""
    skill = STAGE_SKILLS[stage]
    spec = STAGE_ARTIFACTS[stage]
    base = f"/mnt/user-data/outputs/{out_subdir}".rstrip("/")
    lines = [f"/{skill} {question}", ""]
    lines.append(f"Stage: {stage}. Write ALL deliverables to {base}/ with EXACTLY these filenames:")
    for name in spec["required"]:
        lines.append(f"  - {base}/{name}  (REQUIRED — the pipeline fails without it)")
    for name in spec["optional"]:
        lines.append(f"  - {base}/{name}  (optional)")
    lines.append("Previously produced stage artifacts are in /mnt/user-data/outputs/ — read, do not rewrite them.")
    return "\n".join(lines)


class PipelineDriver:
    """确定性管线驱动器。gate 判 fail → 阶段失败（诚实失败优于虚假完成）。"""

    def __init__(self, store: StateStore, harness: Harness,
                 sim_engine: Optional[SimEngine] = None, *,
                 gate_config: Optional[dict[str, Any]] = None,
                 stage_timeouts_s: Optional[dict[str, float]] = None,
                 sim_config: Optional[dict[str, float]] = None,
                 extremize_a: Optional[float] = None):
        self.store = store
        self.harness = harness
        self.sim_engine = sim_engine
        self.gate_config = {**DEFAULT_GATE_CONFIG, **(gate_config or {})}
        self.stage_timeouts_s = {**DEFAULT_STAGE_TIMEOUTS_S, **(stage_timeouts_s or {})}
        self.sim_config = {**DEFAULT_SIM_CONFIG, **(sim_config or {})}
        self.extremize_a = extremize_a

    # ------------------------------------------------------------------ entry points
    def run(self, question: str, *, pipeline_id: Optional[str] = None,
            seeds: Optional[list[int]] = None) -> DriverState:
        state = DriverState.new(question, pipeline_id=pipeline_id)
        state.seeds = list(seeds or [])
        self.store.save(state)
        return self._run_stages(state)

    def resume(self, pipeline_id: str) -> DriverState:
        state = self.store.load(pipeline_id)
        if state.status == "completed":
            return state
        state.status = "running"
        state.error = None
        return self._run_stages(state)

    def status(self, pipeline_id: str) -> dict[str, Any]:
        return self.store.load(pipeline_id).to_dict()

    # ------------------------------------------------------------------ helpers
    def _manifest(self, state: DriverState) -> ArtifactManifest:
        return ArtifactManifest(self.store.manifest_path(state.pipeline_id))

    def _artifact_specs(self, stage: str, subdir: str = "") -> dict[str, str]:
        spec = STAGE_ARTIFACTS[stage]
        out: dict[str, str] = {}
        for name in spec["required"] + spec["optional"]:
            rel = f"{subdir}/{name}" if subdir else name
            key = f"{stage}:{rel}"
            out[key] = str(self.harness.artifact_path(rel))
        return out

    def _missing_required(self, stage: str, subdir: str = "") -> list[str]:
        missing = []
        for name in STAGE_ARTIFACTS[stage]["required"]:
            rel = f"{subdir}/{name}" if subdir else name
            if not Path(self.harness.artifact_path(rel)).exists():
                missing.append(rel)
        return missing

    def _outputs_dir(self, subdir: str = "") -> str:
        return str(Path(self.harness.artifact_path(subdir or ".")).resolve())

    def _stage_gate(self, stage: str, subdir: str = "") -> _gates.GateResult:
        gc = self.gate_config
        out_dir = self._outputs_dir(subdir)
        if stage == "research":
            return _gates.research_gate(out_dir, min_report_chars=int(gc["min_report_chars"]),
                                        quality_floor=float(gc["research_quality_floor"]))
        if stage == "report":
            deliv = _gates.deliverable_gate(
                out_dir, structured_forecast_required=bool(gc["structured_forecast_required"]),
                min_report_bytes=int(gc["min_report_bytes"]))
            conv = _gates.binary_conviction_gate(str(Path(out_dir) / "forecast.json"),
                                                 min_count=int(gc["binary_min_count"]))
            status = deliv.status if deliv.status == "fail" else (
                "degraded" if (deliv.issues or conv.issues) else "pass")
            return _gates.GateResult("report", status, deliv.issues + conv.issues,
                                     {**deliv.meta, "binary_quality": conv.meta})
        # ontology/graph/prepare：产物存在性 + manifest 探针即为门（无额外语义门）。
        return _gates.GateResult(stage, "pass", [], {})

    # ------------------------------------------------------------------ main loop
    def _run_stages(self, state: DriverState) -> DriverState:
        self.harness.ensure_thread()
        state.thread_id = getattr(self.harness, "thread_id", state.thread_id) or state.thread_id
        manifest = self._manifest(state)
        done_now: set[str] = set()
        for stage in STAGES:
            rec = state.stages[stage]
            if rec.status == "completed" and stage not in done_now:
                ok, problems = manifest.verify_stage(stage, self._artifact_specs(stage))
                if ok:
                    state.mark_stage_reused(stage)
                    self.store.save(state)
                    continue
                logger.warning("[%s] stage %s 复用校验失败，强制重建: %s",
                               state.pipeline_id, stage, problems)
                rec.message = "复用校验失败 → 重建：" + "；".join(problems)
            elif rec.status == "completed":
                continue  # 本次会话刚完成（ensemble 联动完成 run+report）
            try:
                if stage == "run" and state.seeds:
                    self._execute_ensemble(state, manifest)
                    done_now.update({"run", "report"})
                elif stage == "run":
                    self._execute_run(state, manifest)
                    done_now.add(stage)
                else:
                    self._execute_harness_stage(state, manifest, stage)
                    done_now.add(stage)
            except Exception as e:  # noqa: BLE001 — 任何阶段异常 = 诚实失败，落盘后停
                for name, r in state.stages.items():  # ensemble 会同时挂起 run+report
                    if r.status == "running":
                        state.fail_stage(name, f"{type(e).__name__}: {e}")
                state.status = "failed"
                state.error = state.error or f"[{stage}] {e}"
                self.store.save(state)
                return state
            self.store.save(state)
            if state.status == "failed":
                return state
        if state.status != "failed":
            state.mark_completed()
            self.store.save(state)
        return state

    # ------------------------------------------------------------------ stage executors
    def _execute_harness_stage(self, state: DriverState, manifest: ArtifactManifest,
                               stage: str, subdir: str = "") -> None:
        state.start_stage(stage)
        self.store.save(state)
        prompt = build_stage_prompt(stage, state.question, subdir)
        result = self.harness.run_stage(stage, prompt,
                                        timeout_s=float(self.stage_timeouts_s[stage]))
        if not result.ok:
            state.fail_stage(stage, result.error or f"harness run status={result.status}")
            return
        missing = self._missing_required(stage, subdir)
        if missing:
            state.fail_stage(stage, "required artifacts missing: " + ", ".join(missing))
            return
        gate = self._stage_gate(stage, subdir)
        if gate.hard_fail:
            state.fail_stage(stage, "gate failed: " + "；".join(gate.issues))
            return
        artifacts = manifest.record_stage(stage, self._artifact_specs(stage, subdir))
        state.complete_stage(stage, health="ok" if gate.status == "pass" else "degraded",
                             issues=gate.issues, artifacts=artifacts)

    def _sim_once(self, seed: Optional[int], subdir: str = "") -> _gates.GateResult:
        """启动一次模拟并轮询到终态；把 run_summary 固化进线程工作区供报告技能引用。"""
        if self.sim_engine is None:
            raise RuntimeError("sim_engine not configured — RUN stage cannot execute")
        cfg_path = Path(self.harness.artifact_path("simulation_config.json"))
        with open(cfg_path, encoding="utf-8") as f:
            sim_cfg = json.load(f)
        sim_id = self.sim_engine.sim_start(sim_cfg, seed=seed)
        sc = self.sim_config
        final = poll_simulation(self.sim_engine, sim_id, timeout_s=float(sc["timeout_s"]),
                                stall_s=float(sc["stall_s"]), interval_s=float(sc["interval_s"]))
        summary = final.get("run_summary") if isinstance(final.get("run_summary"), dict) else None
        rel = f"{subdir}/run_summary.json" if subdir else "run_summary.json"
        if summary is not None:
            write_json_atomic(str(self.harness.artifact_path(rel)), summary)
        elif final.get("sim_dir"):
            src = Path(str(final["sim_dir"])) / "run_summary.json"
            if src.exists():
                write_json_atomic(str(self.harness.artifact_path(rel)),
                                  json.loads(src.read_text(encoding="utf-8")))
        err = final.get("error")
        if err and summary is None and not final.get("sim_dir"):
            # 引擎没跑出任何可评估产物 → 硬失败（区别于「跑完但空心」的 degraded 决策）。
            raise RuntimeError(f"simulation failed with no artifacts: {err}")
        return _gates.hollow_sim_gate(
            sim_dir=str(final["sim_dir"]) if final.get("sim_dir") else None,
            summary=summary,
            run_state={"error": err, "current_round": final.get("current_round"),
                       "total_rounds": final.get("total_rounds")} if final else None)

    def _execute_run(self, state: DriverState, manifest: ArtifactManifest) -> None:
        state.start_stage("run")
        self.store.save(state)
        gate = self._sim_once(None)
        missing = self._missing_required("run")
        if missing:
            state.fail_stage("run", "required artifacts missing: " + ", ".join(missing))
            return
        artifacts = manifest.record_stage("run", self._artifact_specs("run"))
        state.complete_stage("run", health="ok" if gate.status == "pass" else "degraded",
                             issues=gate.issues, artifacts=artifacts)

    def _execute_ensemble(self, state: DriverState, manifest: ArtifactManifest) -> None:
        """多种子扇出：每个种子 = 一次模拟 + 一次报告 run（seed-<n>/ 子目录隔离），
        成功种子的 forecast.json 做 log-odds 池化 → ensemble_forecast.json。"""
        state.start_stage("run")
        state.start_stage("report")
        self.store.save(state)
        run_issues: list[str] = []
        report_issues: list[str] = []

        def _one(seed: int) -> dict[str, Any]:
            subdir = f"seed-{seed}"
            Path(self.harness.artifact_path(subdir)).mkdir(parents=True, exist_ok=True)
            g_run = self._sim_once(seed, subdir)
            run_issues.extend(f"seed {seed}: {i}" for i in g_run.issues)
            prompt = build_stage_prompt("report", state.question, subdir)
            result = self.harness.run_stage("report", prompt,
                                            timeout_s=float(self.stage_timeouts_s["report"]))
            if not result.ok:
                raise RuntimeError(result.error or f"report run status={result.status}")
            out_dir = self._outputs_dir(subdir)
            gc = self.gate_config
            deliv = _gates.deliverable_gate(
                out_dir, structured_forecast_required=bool(gc["structured_forecast_required"]),
                min_report_bytes=int(gc["min_report_bytes"]))
            if deliv.hard_fail:
                raise RuntimeError("deliverable gate failed: " + "；".join(deliv.issues))
            report_issues.extend(f"seed {seed}: {i}" for i in deliv.issues)
            with open(Path(out_dir) / "forecast.json", encoding="utf-8") as f:
                return json.load(f)

        outcome = fan_out(state.seeds, _one, extremize_a=self.extremize_a)
        state.options["ensemble"] = {"seeds": outcome["seeds"], "results": outcome["results"],
                                     "n_ok": outcome["n_ok"]}
        if outcome["n_ok"] == 0:
            errs = "；".join(str(r.get("error")) for r in outcome["results"].values())
            state.fail_stage("run", f"all {len(state.seeds)} ensemble seeds failed: {errs}")
            # report 阶段同步落败（它没有独立可交付物）
            rep = state.stages["report"]
            rep.status = "failed"
            rep.error = "ensemble produced no forecasts"
            rep.health = "failed"
            from .state import utcnow
            rep.finished_at = utcnow()
            return
        ens_path = str(self.harness.artifact_path("ensemble_forecast.json"))
        write_json_atomic(ens_path, outcome["aggregate"])
        specs: dict[str, str] = {"report:ensemble_forecast.json": ens_path}
        for seed in state.seeds:
            specs.update(self._artifact_specs("report", f"seed-{seed}"))
        rec_report = manifest.record_stage("report", specs)
        run_specs: dict[str, str] = {}
        for seed in state.seeds:
            run_specs.update(self._artifact_specs("run", f"seed-{seed}"))
        rec_run = manifest.record_stage("run", run_specs)
        failed = [s for s, r in outcome["results"].items() if not r.get("ok")]
        if failed:
            run_issues.append(f"{len(failed)}/{len(state.seeds)} seeds failed: {', '.join(failed)}")
        state.complete_stage("run", health="degraded" if run_issues else "ok",
                             issues=run_issues, artifacts=rec_run)
        state.complete_stage("report", health="degraded" if (report_issues or failed) else "ok",
                             issues=report_issues, artifacts=rec_report)
