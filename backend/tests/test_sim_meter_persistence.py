"""DEFECT-3：模拟子进程的 token 计量落盘 + 编排器恰好一次入账。

审计取证：648 次已确认的 LLM 调用在 run_telemetry.json 里记为 0 token——oasis_llm
为每次调用构造 usage（CLI/回退路径按文本长度估算、直连路径为提供方真实值），但喂给
camel 后即被丢弃；决策通道/in-band 演化的 LLMMeter 记录只活在子进程内存里，进程退出
整场账蒸发。覆盖：

- 子进程侧累计器：模型边界（_wrap_model_llm_counter）逐调用提取 usage，真实
  （source='provider'）与伪造估算（source='estimate'，id 前缀 'chatcmpl-cli-'）分桶，
  无 usage → 'missing'；既有 counter dict 契约（{"calls","errors"} 精确形状）不变；
- LLMClient 包装（决策通道/in-band 路径）：精确 _last_usage 优先，缺失时长度估算；
- sim_llm_telemetry.json 原子快照：字段、meter_run_token（幂等去重键）、provider 解析、
  失败路径（__main__ finally）照写；
- 重跑轮转：sim_llm_telemetry.json 随 _rotate_stale_action_logs 一并轮转；
- 编排器 _record_sim_run_telemetry：恰好一次（同 token 重入/跨 attempt 复用跳过；
  新 token 的重跑照记）、stage='run' 合成记录、provider/model 映射、零 token 跳过、
  计量关闭仅 stash、失败边界同样恰好一次落账；
- 子进程写 → 编排器读的端到端字段兼容。

全部离线：零网络、零真实 LLM、零真实子进程。
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
for _p in (_BACKEND, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_parallel_simulation as rps  # noqa: E402

from app.config import Config  # noqa: E402
from app.services import pipeline_orchestrator as po  # noqa: E402
from app.services.simulation_runner import SimulationRunner  # noqa: E402
from app.utils.telemetry import LLMMeter  # noqa: E402

SIM_ID = "sim_meter_fixture"


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def fresh_usage(monkeypatch):
    """进程级累计器隔离：每测一个全新 dict（helpers 经模块名解引用，setattr 即生效）。"""
    fresh = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "by_source": {}, "by_model": {}}
    monkeypatch.setattr(rps, "_SIM_LLM_USAGE", fresh)
    return fresh


@pytest.fixture
def meter_run():
    created = []

    def _make(run_id):
        created.append(run_id)
        LLMMeter.reset(run_id)
        return run_id

    yield _make
    for rid in created:
        LLMMeter.reset(rid)


@pytest.fixture
def orch_env(monkeypatch, tmp_path):
    """编排器侧隔离：RUN_STATE_DIR + PIPELINE_DATA_DIR + 计量开。"""
    root = tmp_path / "simulations"
    (root / SIM_ID).mkdir(parents=True)
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(root))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"),
                        raising=False)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", True, raising=False)
    return str(root / SIM_ID)


def _completion(pt=100, ct=40, cid="chatcmpl-real-1", model="MiniMax-M3"):
    return SimpleNamespace(
        id=cid, model=model,
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct,
                              total_tokens=pt + ct),
    )


def _write_snapshot(sim_dir, **overrides):
    payload = {
        "schema_version": "sim-llm-telemetry/v1",
        "simulation_id": SIM_ID,
        "meter_run_token": "tok_attempt_1",
        "provider": "minimax",
        "model": "MiniMax-M3",
        "calls": 648,
        "errors": 2,
        "prompt_tokens": 120000,
        "completion_tokens": 45000,
        "total_tokens": 165000,
        "by_source": {"provider": {"calls": 600, "prompt_tokens": 110000,
                                   "completion_tokens": 41000},
                      "estimate": {"calls": 48, "prompt_tokens": 10000,
                                   "completion_tokens": 4000}},
        "wall_s": 2880.0,
    }
    payload.update(overrides)
    with open(os.path.join(sim_dir, "sim_llm_telemetry.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload


# ---------------------------------------------------- 子进程侧：来源分桶
def test_accumulator_separates_provider_and_estimate_sources(fresh_usage):
    rps._accumulate_sim_llm_response(_completion(100, 40, cid="chatcmpl-real-1"))
    rps._accumulate_sim_llm_response(
        _completion(30, 10, cid="chatcmpl-cli-abcdef", model="claude-cli"))
    rps._accumulate_sim_llm_response(
        SimpleNamespace(id="x", model="m", usage=None))

    u = rps._SIM_LLM_USAGE
    assert u["calls"] == 3
    assert u["prompt_tokens"] == 130 and u["completion_tokens"] == 50
    assert u["by_source"]["provider"] == {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 40}
    assert u["by_source"]["estimate"] == {
        "calls": 1, "prompt_tokens": 30, "completion_tokens": 10}
    assert u["by_source"]["missing"]["calls"] == 1
    assert u["by_model"]["MiniMax-M3"]["calls"] == 1
    assert u["by_model"]["claude-cli"]["calls"] == 1


def test_accumulator_never_raises_on_garbage(fresh_usage):
    rps._accumulate_sim_llm_response(None)
    rps._accumulate_sim_llm_response({"ok": True})
    rps._accumulate_sim_llm_response(
        SimpleNamespace(id=None, model=None,
                        usage=SimpleNamespace(prompt_tokens="junk",
                                              completion_tokens=None)))
    assert rps._SIM_LLM_USAGE["calls"] >= 2  # 垃圾输入也只是记 0-token 调用，不炸


def test_wrap_model_counter_accumulates_usage_and_keeps_contract(fresh_usage):
    """模型边界包装：usage 进累计器，counter dict 保持 {"calls","errors"} 精确形状
    （test_audit_fixes_runloop 的既有契约按 dict 相等断言）。"""
    class FakeModel:
        behavior = ["ok", "fail", "ok"]

        async def _arequest_chat_completion(self, messages, tools=None):
            if self.behavior.pop(0) == "fail":
                raise ValueError("boom")
            return _completion(50, 20)

    model = FakeModel()
    counter = rps._wrap_model_llm_counter(model)

    async def _drive():
        await model._arequest_chat_completion([])
        with pytest.raises(ValueError):
            await model._arequest_chat_completion([])
        await model._arequest_chat_completion([])

    asyncio.run(_drive())
    assert counter == {"calls": 3, "errors": 1}  # 契约不变（无新键）
    u = rps._SIM_LLM_USAGE
    assert u["calls"] == 2 and u["errors"] == 1
    assert u["prompt_tokens"] == 100 and u["completion_tokens"] == 40
    assert u["by_source"]["provider"]["calls"] == 2


def test_wrap_llm_client_provider_usage_preferred(fresh_usage):
    class FakeClient:
        provider = "minimax"
        model = "MiniMax-M3"
        _last_usage = None

        def chat(self, messages, **kwargs):
            self._last_usage = {"prompt_tokens": 800, "completion_tokens": 150}
            return "batch decision text"

    client = rps._wrap_llm_client_usage(FakeClient())
    assert client.chat([{"role": "user", "content": "q"}]) == "batch decision text"
    u = rps._SIM_LLM_USAGE
    assert u["by_source"]["provider"] == {
        "calls": 1, "prompt_tokens": 800, "completion_tokens": 150}
    assert u["by_model"]["MiniMax-M3"]["calls"] == 1


def test_wrap_llm_client_estimates_when_no_usage(fresh_usage):
    class FakeCLIClient:
        provider = "claude-cli"
        model = ""
        _last_usage = None

        def chat_json(self, messages, **kwargs):
            return {"commitments": ["scenario_a"] * 10}

    client = rps._wrap_llm_client_usage(FakeCLIClient())
    client.chat_json([{"role": "user", "content": "x" * 400}])
    u = rps._SIM_LLM_USAGE
    est = u["by_source"]["estimate"]
    assert est["calls"] == 1
    assert est["prompt_tokens"] > 0 and est["completion_tokens"] > 0


def test_wrap_llm_client_counts_errors_and_reraises(fresh_usage):
    class Broken:
        provider = "minimax"
        model = "m"
        _last_usage = None

        def chat(self, messages, **kwargs):
            raise RuntimeError("429 quota")

    client = rps._wrap_llm_client_usage(Broken())
    with pytest.raises(RuntimeError, match="429"):
        client.chat([])
    assert rps._SIM_LLM_USAGE["errors"] == 1
    assert rps._SIM_LLM_USAGE["calls"] == 0


# ---------------------------------------------------- 子进程侧：快照落盘
def test_write_snapshot_fields_and_token(fresh_usage, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    rps._accumulate_sim_llm_response(_completion(100, 40))
    rps._accumulate_sim_llm_response(
        _completion(30, 10, cid="chatcmpl-cli-x", model="claude-cli"))
    rps._write_sim_llm_telemetry(str(tmp_path), {"simulation_id": SIM_ID})

    with open(tmp_path / "sim_llm_telemetry.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema_version"] == "sim-llm-telemetry/v1"
    assert data["simulation_id"] == SIM_ID
    assert data["provider"] == "minimax"
    assert data["model"] in ("MiniMax-M3", "claude-cli")  # 主导模型（并列取其一）
    assert data["calls"] == 2 and data["errors"] == 0
    assert data["prompt_tokens"] == 130 and data["completion_tokens"] == 50
    assert data["total_tokens"] == 180
    assert data["by_source"]["provider"]["calls"] == 1
    assert data["by_source"]["estimate"]["calls"] == 1
    assert data["meter_run_token"] == rps._SIM_LLM_METER_RUN_TOKEN
    assert data["wall_s"] >= 0

    # 幂等重写：同进程 token 不变（编排器据此去重）。
    rps._write_sim_llm_telemetry(str(tmp_path), {"simulation_id": SIM_ID})
    with open(tmp_path / "sim_llm_telemetry.json", encoding="utf-8") as f:
        again = json.load(f)
    assert again["meter_run_token"] == data["meter_run_token"]


def test_write_snapshot_on_failure_path_with_zero_calls(fresh_usage, tmp_path):
    """失败路径（__main__ finally）：即使一发调用都没打出去也照写快照（诚实的 0 账）。"""
    rps._write_sim_llm_telemetry(str(tmp_path), None)
    with open(tmp_path / "sim_llm_telemetry.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["calls"] == 0 and data["total_tokens"] == 0


def test_main_exit_wiring_writes_snapshot_in_finally():
    """__main__ 出口接线特征化：finally 块里挂 _write_sim_llm_telemetry（失败/中断/
    信号退出路径都不丢账），且 main() 尽早钉定落盘目标。"""
    with open(os.path.join(_SCRIPTS, "run_parallel_simulation.py"),
              encoding="utf-8") as f:
        src = f.read()
    i_main_guard = src.index('if __name__ == "__main__":')
    tail = src[i_main_guard:]
    i_finally = tail.index("finally:")
    assert "_write_sim_llm_telemetry(" in tail[i_finally:]
    assert '_SIM_LLM_TELEMETRY_SINK["dir"] = simulation_dir' in src
    # 模拟回路结束后（进入命令等待模式之前）也先落一版。
    i_loop_done = src.index("模拟循环完成! 总耗时")  # main() 的双平台收束点
    i_early_write = src.index("_write_sim_llm_telemetry(simulation_dir, config",
                              i_loop_done)
    assert i_early_write < src.index("进入等待命令模式 - 环境保持运行", i_loop_done)


def test_rotate_stale_artifacts_rotates_snapshot(tmp_path, monkeypatch):
    """重跑前上一轮的 token 快照必须轮转——防止新一轮启动即失败时旧账被再次消费。"""
    sim_dir = tmp_path / SIM_ID
    sim_dir.mkdir()
    (sim_dir / "sim_llm_telemetry.json").write_text("{}", encoding="utf-8")
    SimulationRunner._rotate_stale_action_logs(str(sim_dir))
    assert not (sim_dir / "sim_llm_telemetry.json").exists()
    assert (sim_dir / "sim_llm_telemetry.json.prev").exists()


# ---------------------------------------------------- 编排器侧：恰好一次入账
def test_orchestrator_lands_run_stage_record_exactly_once(orch_env, meter_run):
    run_id = meter_run("pipe_simmeter1")
    _write_snapshot(orch_env)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    orch = po.PipelineOrchestrator()

    orch._record_sim_run_telemetry(state, SIM_ID)
    snap = LLMMeter.snapshot(run_id)
    run_row = snap["by_stage"]["run"]
    assert run_row["calls"] == 1                       # 一条合成记录
    assert run_row["prompt_tokens"] == 120000
    assert run_row["completion_tokens"] == 45000
    assert "minimax:MiniMax-M3" in snap["by_model"]

    # 重入（同一边界被再次触达 / 复用路径）→ 同 token 跳过，无双计。
    orch._record_sim_run_telemetry(state, SIM_ID)
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["run"]["calls"] == 1
    assert snap["total"]["calls"] == 1

    stash = state.options["sim_llm_telemetry"]
    assert stash["calls"] == 648 and stash["total_tokens"] == 165000
    assert stash["by_source"]["estimate"]["calls"] == 48
    marker = state.options["sim_llm_telemetry_recorded"]
    assert marker["simulation_id"] == SIM_ID
    assert marker["meter_run_token"] == "tok_attempt_1"


def test_orchestrator_skips_after_prior_attempt_marker(orch_env, meter_run):
    """resume 复用路径：上一 attempt 已入账（marker 随 pipeline_state 持久化）→
    本 attempt 的边界调用跳过（上一 attempt 的账经 run_telemetry 合并基底延续）。"""
    run_id = meter_run("pipe_simmeter2")
    _write_snapshot(orch_env)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    state.options["sim_llm_telemetry_recorded"] = {
        "simulation_id": SIM_ID, "meter_run_token": "tok_attempt_1",
        "recorded_at": "2026-07-15T00:00:00Z"}
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0


def test_rerun_with_new_token_records_the_new_spend(orch_env, meter_run):
    """重跑（rotation 后新子进程 = 新 meter_run_token）是新一笔真实花费 → 照记。"""
    run_id = meter_run("pipe_simmeter3")
    _write_snapshot(orch_env)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    orch = po.PipelineOrchestrator()
    orch._record_sim_run_telemetry(state, SIM_ID)
    _write_snapshot(orch_env, meter_run_token="tok_attempt_2",
                    prompt_tokens=5000, completion_tokens=2000,
                    total_tokens=7000, calls=30)
    orch._record_sim_run_telemetry(state, SIM_ID)
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["run"]["calls"] == 2
    assert snap["by_stage"]["run"]["prompt_tokens"] == 125000
    assert state.options["sim_llm_telemetry_recorded"]["meter_run_token"] == "tok_attempt_2"


def test_failed_sim_snapshot_lands_exactly_once_at_failure_boundary(
        orch_env, meter_run):
    """失败模拟：子进程 finally 已落盘快照 → 失败边界（except BaseException 钩子）
    入账恰好一次；随后 resume 重跑前的任何重入不双计。"""
    run_id = meter_run("pipe_simmeter4")
    _write_snapshot(orch_env, meter_run_token="tok_failed_attempt",
                    calls=210, prompt_tokens=40000, completion_tokens=9000,
                    total_tokens=49000)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    orch = po.PipelineOrchestrator()
    orch._record_sim_run_telemetry(state, SIM_ID)   # 失败边界
    orch._record_sim_run_telemetry(state, SIM_ID)   # 冗余重入
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["run"]["calls"] == 1
    assert snap["by_stage"]["run"]["prompt_tokens"] == 40000


def test_zero_token_snapshot_marks_but_records_nothing(orch_env, meter_run):
    run_id = meter_run("pipe_simmeter5")
    _write_snapshot(orch_env, calls=3, prompt_tokens=0, completion_tokens=0,
                    total_tokens=0)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0
    # 已核销（marker 落下）：不再反复重读同一份空账。
    assert state.options["sim_llm_telemetry_recorded"]["meter_run_token"] == "tok_attempt_1"


def test_missing_snapshot_is_silent_noop(orch_env, meter_run):
    run_id = meter_run("pipe_simmeter6")
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0
    assert "sim_llm_telemetry_recorded" not in state.options


def test_telemetry_disabled_stashes_without_meter_record(
        orch_env, meter_run, monkeypatch):
    run_id = meter_run("pipe_simmeter7")
    _write_snapshot(orch_env)
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", False, raising=False)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0
    assert state.options["sim_llm_telemetry"]["calls"] == 648  # 免费观测仍在


def test_cli_model_fallback_provider_mapping(orch_env, meter_run):
    """快照缺 provider 时按研究路径同款映射兜底：claude/codex → claude-cli（0 成本）。"""
    run_id = meter_run("pipe_simmeter8")
    _write_snapshot(orch_env, provider="", model="claude",
                    prompt_tokens=100, completion_tokens=50, total_tokens=150)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    snap = LLMMeter.snapshot(run_id)
    assert "claude-cli:claude" in snap["by_model"]
    assert snap["total"]["cost_usd"] == 0


def test_subprocess_writer_to_orchestrator_roundtrip(
        fresh_usage, orch_env, meter_run, monkeypatch):
    """端到端字段兼容：子进程 writer 落盘 → 编排器读同一文件入账。"""
    run_id = meter_run("pipe_simmeter9")
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    rps._accumulate_sim_llm_response(_completion(700, 300))
    rps._write_sim_llm_telemetry(orch_env, {"simulation_id": SIM_ID})

    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_sim_run_telemetry(state, SIM_ID)
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["run"]["calls"] == 1
    assert snap["by_stage"]["run"]["prompt_tokens"] == 700
    assert snap["by_stage"]["run"]["completion_tokens"] == 300
    assert "minimax:MiniMax-M3" in snap["by_model"]
    assert (state.options["sim_llm_telemetry_recorded"]["meter_run_token"]
            == rps._SIM_LLM_METER_RUN_TOKEN)
