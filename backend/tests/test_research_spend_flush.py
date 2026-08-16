"""DEFECT-2：失败/超时/取消的研究 attempt 的 token 花费不再丢账。

审计取证（31.9M 实耗 vs 5.35M 入账）：per-attempt 的 _tok_in/_tok_out 在每次研究子进程
启动时清零、research_telemetry 只在 runner 成功路径构建、_record_research_telemetry 是
研究花费进 LLMMeter 的唯一入口——失败/超时/SIGTERM/resume（telemetry=None）把整个
attempt 的账直接丢掉。覆盖：

- 真实 _run_attempt 失败路径（假子进程发出 [usage] 行后非零退出）→ 该 attempt 的 token
  恰好一次进入 stage='research' 计量；
- 成功路径逐字节不变：runner 不计量，仍由调用方经 _record_research_telemetry 恰好记一次
  （无双计）；
- 包装层对 PipelineCancelled 的 flush；
- _flush_failed_research_attempt_spend 的恰好一次守卫 / 零 token 跳过 / 计量关闭跳过 /
  provider 映射与成功路径一致；
- resume 复用路径（telemetry=None）不再抹掉此前失败 attempt 已入账的花费。

全部离线：假 Popen、零网络、零真实 LLM。
"""

import time

import pytest

from app.config import Config
from app.services import pipeline_orchestrator as po
from app.utils.telemetry import LLMMeter


class FakeProc:
    """最小假研究子进程：喂给读循环的 stdout 行 + 固定退出码。"""

    def __init__(self, lines, returncode):
        self.pid = 4321
        self.stdout = list(lines)
        self._returncode = returncode

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode


def _wire_fake_subprocess(monkeypatch, tmp_path, lines, returncode):
    deerflow_dir = tmp_path / "deer-flow"
    deerflow_dir.mkdir()
    (deerflow_dir / "deerflow_research.py").write_text(
        "# test entrypoint\n", encoding="utf-8")
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    monkeypatch.setattr(po.Config, "DEERFLOW_DIR", str(deerflow_dir))
    monkeypatch.setattr(po.Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(po, "_sync_deerflow_bridge_if_stale", lambda _p: None)
    proc = FakeProc(lines, returncode)
    monkeypatch.setattr(po.subprocess, "Popen", lambda *a, **k: proc)
    return handoff


@pytest.fixture
def meter_run():
    """每测一个独立 run id，测试后清账（LLMMeter 是进程级累加器）。"""
    created = []

    def _make(run_id):
        created.append(run_id)
        LLMMeter.reset(run_id)
        return run_id

    yield _make
    for rid in created:
        LLMMeter.reset(rid)


USAGE_LINES = [
    "[init] starting research\n",
    "[usage] tokens in=12000 out=3400 total=15400\n",
    "[tool] web_search q=...\n",
    "[usage] tokens in=10000 out=2600 total=12600\n",
]


# ---------------------------------------------- 失败 attempt：恰好一次入账
def test_failed_attempt_flushes_spend_to_meter_exactly_once(
        monkeypatch, tmp_path, meter_run):
    run_id = meter_run("pipe_spendfail")
    handoff = _wire_fake_subprocess(
        monkeypatch, tmp_path,
        USAGE_LINES + ["[error] provider 429 quota exhausted\n"],
        returncode=3,
    )

    with pytest.raises(RuntimeError, match="研究子进程失败"):
        po.DeerFlowResearchRunner.run(
            "Will X happen?",
            str(handoff),
            on_progress=lambda _p, _m: None,
            timeout=10,
            model="claude",
            budget_run_id=run_id,
        )

    snap = LLMMeter.snapshot(run_id)
    research = snap["by_stage"]["research"]
    assert research["calls"] == 1                       # 恰好一条合成记录
    assert research["prompt_tokens"] == 22000           # 两条 [usage] 累加
    assert research["completion_tokens"] == 6000
    assert snap["total"]["calls"] == 1                  # 无重复记录
    # provider 映射与成功路径（_record_research_telemetry）一致：claude → claude-cli
    assert "claude-cli:claude" in snap["by_model"]


def test_failed_attempt_without_usage_lines_records_nothing(
        monkeypatch, tmp_path, meter_run):
    """研究模型不报 usage → 无可计量 token → 不写空记录（与成功路径同语义）。"""
    run_id = meter_run("pipe_spendnousage")
    handoff = _wire_fake_subprocess(
        monkeypatch, tmp_path, ["[init] starting\n", "[error] boom\n"],
        returncode=2,
    )
    with pytest.raises(RuntimeError, match="研究子进程失败"):
        po.DeerFlowResearchRunner.run(
            "Q", str(handoff),
            on_progress=lambda _p, _m: None,
            timeout=10, budget_run_id=run_id,
        )
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0


# ---------------------------------------------- 成功路径：逐字节不变、无双计
def test_success_path_records_once_via_caller_no_double_count(
        monkeypatch, tmp_path, meter_run):
    run_id = meter_run("pipe_spendok")
    handoff = _wire_fake_subprocess(
        monkeypatch, tmp_path, USAGE_LINES + ["[done] research complete\n"],
        returncode=0,
    )
    (handoff / "research_report.md").write_text(
        "research evidence " * 60, encoding="utf-8")

    res = po.DeerFlowResearchRunner.run(
        "Will X happen?",
        str(handoff),
        on_progress=lambda _p, _m: None,
        timeout=10,
        model="claude",
        budget_run_id=run_id,
    )

    tel = res["research_telemetry"]
    assert tel["tokens_in"] == 22000 and tel["tokens_out"] == 6000
    # 成功 return：runner 不计量（仍由调用方入账）→ 此刻 meter 应为空
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0

    # 调用方（_run 的唯一入账口）记一次 → 恰好一条 stage='research' 记录
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    from app.utils import telemetry as _t
    _t.set_run_context(run_id)
    try:
        po.PipelineOrchestrator()._record_research_telemetry(state, tel)
    finally:
        _t.set_run_context(None)
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["research"]["calls"] == 1
    assert snap["by_stage"]["research"]["prompt_tokens"] == 22000
    assert state.options["research_telemetry"] == tel


# ---------------------------------------------- 包装层：取消也不丢账
def test_cancelled_attempt_flushes_via_wrapper(monkeypatch, meter_run):
    run_id = meter_run("pipe_spendcancel")

    def fake_attempt(prompt, handoff_dir, *, _spend=None, **kwargs):
        _spend["tokens_in"] = 500
        _spend["tokens_out"] = 250
        raise po.PipelineCancelled("深度研究已取消")

    monkeypatch.setattr(
        po.DeerFlowResearchRunner, "_run_attempt", staticmethod(fake_attempt))
    with pytest.raises(po.PipelineCancelled):
        po.DeerFlowResearchRunner.run(
            "Q", "/nonexistent",
            on_progress=lambda _p, _m: None,
            model="claude",
            budget_run_id=run_id,
        )
    research = LLMMeter.snapshot(run_id)["by_stage"]["research"]
    assert research["calls"] == 1
    assert research["prompt_tokens"] == 500
    assert research["completion_tokens"] == 250


# ---------------------------------------------- flush 帮手：恰好一次 + 边界
def test_flush_helper_exactly_once_guard(meter_run):
    run_id = meter_run("pipe_spendonce")
    spend = {"tokens_in": 100, "tokens_out": 50, "t_start": time.time(),
             "model": "claude", "flushed": False}
    assert po._flush_failed_research_attempt_spend(
        spend, "failed(RuntimeError)", run_id=run_id) is True
    # 二次调用：flushed 守卫 → 不重复入账
    assert po._flush_failed_research_attempt_spend(
        spend, "failed(RuntimeError)", run_id=run_id) is False
    snap = LLMMeter.snapshot(run_id)
    assert snap["by_stage"]["research"]["calls"] == 1
    assert snap["by_stage"]["research"]["prompt_tokens"] == 100


def test_flush_helper_skips_zero_tokens_and_disabled_telemetry(
        monkeypatch, meter_run):
    run_id = meter_run("pipe_spendskip")
    # 零 token → 不写空记录
    assert po._flush_failed_research_attempt_spend(
        {"tokens_in": 0, "tokens_out": 0, "flushed": False},
        "failed", run_id=run_id) is False
    # 计量关闭 → 跳过
    monkeypatch.setattr(Config, "LLM_TELEMETRY_ENABLED", False, raising=False)
    assert po._flush_failed_research_attempt_spend(
        {"tokens_in": 10, "tokens_out": 5, "flushed": False},
        "failed", run_id=run_id) is False
    # 非法输入 → 安静 no-op
    assert po._flush_failed_research_attempt_spend(None, "failed") is False
    assert LLMMeter.snapshot(run_id)["total"]["calls"] == 0


def test_flush_helper_provider_mapping_matches_success_path(meter_run):
    """非 CLI 研究模型（如 minimax）沿用同名 provider 键——与成功路径完全一致。"""
    run_id = meter_run("pipe_spendmap")
    po._flush_failed_research_attempt_spend(
        {"tokens_in": 10, "tokens_out": 5, "t_start": time.time(),
         "model": "minimax", "flushed": False},
        "failed(RuntimeError)", run_id=run_id)
    assert "minimax:minimax" in LLMMeter.snapshot(run_id)["by_model"]


# ------------------------------------ resume（telemetry=None）不再抹掉旧账
def test_resume_none_telemetry_keeps_prior_flushed_spend(
        monkeypatch, tmp_path, meter_run):
    run_id = meter_run("pipe_spendresume")
    # 上一 attempt 失败时已 flush 进 meter
    po._flush_failed_research_attempt_spend(
        {"tokens_in": 1000, "tokens_out": 400, "t_start": time.time(),
         "model": "claude", "flushed": False},
        "failed(RuntimeError)", run_id=run_id)

    # resume 复用研究产物 → telemetry=None：跳过 stash/入账，但不清旧账、不双计
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path), raising=False)
    state = po.PipelineState(pipeline_id=run_id, prompt="q")
    po.PipelineOrchestrator()._record_research_telemetry(state, None)

    research = LLMMeter.snapshot(run_id)["by_stage"]["research"]
    assert research["calls"] == 1
    assert research["prompt_tokens"] == 1000
    assert "research_telemetry" not in state.options
