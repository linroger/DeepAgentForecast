"""B2（三部结构骨架 + 需求书 binary_min_count 生效）与 R2（死抓取程序化重试）的离线测试。

全部无网络：Part 2 综合走 FakeLLMClient；桥接层的重试直抓被打桩。
"""

import importlib.util
from pathlib import Path

import pytest

from app.config import Config
from app.services.report_agent import ReportAgent, ReportManager
from tests.conftest import FakeLLMClient

# ---------------------------------------------------------------- fixtures

_BRIDGE_PATH = (Path(__file__).resolve().parents[2]
                / "deerflow_bridge" / "deerflow_research.py")


@pytest.fixture(scope="module")
def dfr():
    """把 stdlib-only 的桥接模块按路径加载进来（不依赖 DeerFlow venv）。"""
    spec = importlib.util.spec_from_file_location("dfr_under_test", str(_BRIDGE_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReportStub:
    def __init__(self, md):
        self.markdown_content = md


class PlogStub:
    def __init__(self):
        self.lines = []

    def write(self, kind, msg):
        self.lines.append((kind, msg))


_ASSEMBLED_MD = (
    "# Grand Forecast\n"
    "\n"
    "## Part 1 — Binary Forecasts\n"
    "\n"
    "| # | Forecast (one sentence) | Prob. |\n"
    "|---|---|---|\n"
    "| F1 | Tariffs stay above 10% | 25% |\n"
    "\n"
    "> Executive summary blockquote\n"
    "\n"
    "---\n"
    "\n"
    "## Section A\n"
    "\n"
    "Alpha analysis body with plenty of detail on drivers.\n"
    "\n"
    "## Section B\n"
    "\n"
    "Beta analysis body with more detail on risks.\n"
)

_SPINE = {
    "headline": "H", "horizon": "2027", "confidence": "medium",
    "scenarios": [
        {"name": "A", "probability": 0.6, "resolution_criteria": "x > 1 by 2027"},
        {"name": "B", "probability": 0.4, "resolution_criteria": "x <= 1 by 2027"},
    ],
    "binary_forecasts": [{"id": "F1", "statement": "Tariffs stay above 10%",
                          "probability": 0.25}],
}

_SYNTHESIS = ("The framework rests on three drivers connecting tariffs to capex. " * 8)


def _mk_agent(requirement="", llm=None, market_pack="", language="English"):
    """__new__ 构造（与既有 report 测试同模式），只挂骨架所需属性。"""
    a = ReportAgent.__new__(ReportAgent)
    a.output_language = language
    a.simulation_requirement = requirement
    a.research_report = ""
    a._forecast_spine = dict(_SPINE)
    a._market_pack = market_pack
    a.llm = llm or FakeLLMClient(responses=[_SYNTHESIS])
    return a


@pytest.fixture
def report_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "_get_report_folder",
                        classmethod(lambda cls, rid: str(tmp_path)))
    return tmp_path


# --------------------------------------------------- B2: three-part skeleton

def test_skeleton_inserts_part2_and_part3_in_order(report_folder):
    fake = FakeLLMClient(responses=[_SYNTHESIS])
    a = _mk_agent(llm=fake, market_pack="MARKET-PACK mkt-1 30%")
    rep = ReportStub(_ASSEMBLED_MD)
    a._apply_three_part_skeleton("rid-1", rep)
    md = rep.markdown_content
    i_p1 = md.find("## Part 1 — Binary Forecasts")
    i_p2 = md.find("## Part 2 — Framework & Synthesis")
    i_p3 = md.find("## Part 3 — Appendix: Detailed Analysis")
    i_sa = md.find("## Section A")
    assert -1 < i_p1 < i_p2 < i_p3 < i_sa          # Part1 → Part2 → Part3 → 详细章节
    assert _SYNTHESIS.split(".")[0] in md            # 综合正文进稿
    assert md.find("## Section B") > i_p3            # 既有章节全部落在 Part 3 之后
    # ONE LLM call，提示词含骨架/市场包/各章要点与默认词数上限
    assert len(fake.calls) == 1
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "AT MOST 2800 words" in prompt            # RQ-1 默认词数上限 1800→2800
    assert "MARKET-PACK" in prompt
    assert "[Section key points]" in prompt and "Section A" in prompt
    assert "[Forecast spine]" in prompt
    # full_report.md 同步重写
    assert (report_folder / "full_report.md").read_text(encoding="utf-8") == md


def test_skeleton_consumes_page_budget(report_folder):
    fake = FakeLLMClient(responses=[_SYNTHESIS])
    a = _mk_agent(requirement="Please keep the submission to 8 pages or less.", llm=fake)
    a._apply_three_part_skeleton("rid-2", ReportStub(_ASSEMBLED_MD))
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "AT MOST 1200 words" in prompt            # 8 页 × 150 词/页，钳在 [600, 2800]（RQ-1）


def test_skeleton_idempotent_and_single_llm_call(report_folder):
    fake = FakeLLMClient(responses=[_SYNTHESIS, "SHOULD-NOT-BE-USED " * 30])
    a = _mk_agent(llm=fake)
    rep = ReportStub(_ASSEMBLED_MD)
    a._apply_three_part_skeleton("rid-3", rep)
    once = rep.markdown_content
    a._apply_three_part_skeleton("rid-3", rep)       # 第二次：幂等 no-op，无第二次 LLM 调用
    assert rep.markdown_content == once
    assert once.count("## Part 2 — Framework & Synthesis") == 1
    assert len(fake.calls) == 1


def test_skeleton_skips_on_short_or_failed_synthesis(report_folder):
    a = _mk_agent(llm=FakeLLMClient(responses=["too short"]))
    rep = ReportStub(_ASSEMBLED_MD)
    a._apply_three_part_skeleton("rid-4", rep)
    assert rep.markdown_content == _ASSEMBLED_MD     # 跳过：不动稿、绝无占位符
    assert "Part 2" not in rep.markdown_content.replace("Part 1", "")


def test_skeleton_noop_without_part1(report_folder):
    fake = FakeLLMClient(responses=[_SYNTHESIS])
    a = _mk_agent(llm=fake)
    rep = ReportStub("# T\n\n## Section A\n\nbody\n")
    a._apply_three_part_skeleton("rid-5", rep)
    assert "Part 2" not in rep.markdown_content
    assert fake.calls == []                          # 无 Part 1 → 连 LLM 都不调


def test_skeleton_chinese_headings(report_folder):
    md = _ASSEMBLED_MD.replace("## Part 1 — Binary Forecasts",
                               "## 第一部分 · 二元预测（Part 1 — Binary Forecasts）")
    a = _mk_agent(llm=FakeLLMClient(responses=[_SYNTHESIS]), language="Chinese")
    rep = ReportStub(md)
    a._apply_three_part_skeleton("rid-6", rep)
    assert "## 第二部分 · 框架与综合" in rep.markdown_content
    assert "## 第三部分 · 附录：详细分析" in rep.markdown_content


def test_skeleton_strips_model_emitted_part2_heading(report_folder):
    a = _mk_agent(llm=FakeLLMClient(responses=["## Part 2 — Framework & Synthesis\n"
                                               + _SYNTHESIS]))
    rep = ReportStub(_ASSEMBLED_MD)
    a._apply_three_part_skeleton("rid-7", rep)
    assert rep.markdown_content.count("## Part 2 — Framework & Synthesis") == 1


# ----------------------------------------- B2: spec binary_min_count 生效

def test_binary_min_count_takes_max_of_spec_and_config(monkeypatch):
    monkeypatch.setattr(Config, "BINARY_FORECASTS_MIN_COUNT", 10, raising=False)
    a = _mk_agent(requirement="The submission must contain at least 15 binary forecasts.")
    assert a._binary_min_count() == 15               # spec 更高 → spec 胜
    a2 = _mk_agent(requirement="Provide at least 3 binary forecasts.")
    assert a2._binary_min_count() == 10              # spec 更低 → Config 兜底
    a3 = _mk_agent(requirement="No counts mentioned here.")
    assert a3._binary_min_count() == 10              # spec 未解析出 → Config


# ------------------------------------------------ R2: governed dead-fetch handling

def test_dead_fetches_are_not_locally_retried_or_revived(dfr):
    pending = [
        {"url": "http://169.254.169.254/latest/meta-data", "ok": False},
        {"url": "http://allowed.example", "ok": False},
    ]
    before = [dict(row) for row in pending]
    dfr._retry_dead_fetches(pending, PlogStub())
    assert pending == before


def test_structured_policy_and_budget_results_are_dead_nonretryable(dfr):
    for error in (
        "source_quality_rejected",
        "research_budget_exhausted",
        "research_negative_cache_suppressed",
    ):
        pending = [{"url": "https://example.test", "call_id": "x", "ok": None}]
        dfr._pending_mark_result(
            pending,
            "web_fetch",
            '{"error": "' + error + '", "padding": "' + "x" * 300 + '"}',
            call_id="x",
        )
        assert pending[0]["ok"] is False
        assert pending[0]["retryable"] is False


def test_compact_repeat_envelope_is_not_counted_as_a_new_read(dfr):
    pending = [{"url": "https://example.test", "call_id": "x", "ok": None}]
    dfr._pending_mark_result(
        pending,
        "web_fetch",
        '{"status": "already_available", "artifact_id": "fetch:abc", '
        '"padding": "' + "x" * 300 + '"}',
        call_id="x",
    )
    assert pending[0]["ok"] is False
    dfr._reset_fetched_sources()
    dfr._merge_pending_fetches(pending)
    assert dfr._FETCHED_SOURCES == []
