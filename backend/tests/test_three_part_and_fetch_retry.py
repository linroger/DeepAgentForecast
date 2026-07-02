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
    assert "AT MOST 1800 words" in prompt
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
    assert "AT MOST 1200 words" in prompt            # 8 页 × 150 词/页，钳在 [600, 1800]


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


# ------------------------------------------------ R2: dead-fetch retry

def test_fetch_retry_count_env_parsing(dfr, monkeypatch):
    monkeypatch.delenv("DEERFLOW_FETCH_RETRY", raising=False)
    assert dfr._fetch_retry_count() == 1             # 默认 1
    monkeypatch.setenv("DEERFLOW_FETCH_RETRY", "0")
    assert dfr._fetch_retry_count() == 0             # 0 = 关闭
    monkeypatch.setenv("DEERFLOW_FETCH_RETRY", "2")
    assert dfr._fetch_retry_count() == 2
    monkeypatch.setenv("DEERFLOW_FETCH_RETRY", "garbage")
    assert dfr._fetch_retry_count() == 1             # 非法值回落默认


def test_retry_revives_transient_dead_fetch(dfr, monkeypatch):
    monkeypatch.delenv("DEERFLOW_FETCH_RETRY", raising=False)
    monkeypatch.setattr(dfr, "_FETCH_RETRY_BACKOFF_S", 0.0)
    calls = []

    def fake_fetch(url, timeout=15.0):
        calls.append(url)
        if url == "http://alive.example":
            return "real page content " * 30         # >200 字符且无哨兵 → 判活
        raise RuntimeError("still down")

    monkeypatch.setattr(dfr, "_retry_fetch_url", fake_fetch)
    pending = [
        {"url": "http://alive.example", "call_id": "1", "ok": False},   # 瞬时死 → 复活
        {"url": "http://ok.example", "call_id": "2", "ok": True},       # 本来就活 → 不动
        {"url": "http://gone.example", "call_id": "3", "ok": False},    # 重试仍死 → 丢弃
    ]
    plog = PlogStub()
    dfr._retry_dead_fetches(pending, plog)
    assert pending[0]["ok"] is True and pending[0].get("retried") is True
    assert pending[1]["ok"] is True and "retried" not in pending[1]
    assert pending[2]["ok"] is False
    assert sorted(calls) == ["http://alive.example", "http://gone.example"]  # 只重试死行
    # 每个重试过的 URL 恰好一行日志（含结局）
    retry_lines = [m for k, m in plog.lines if k == "retry"]
    assert len(retry_lines) == 2
    assert any("http://alive.example" in m and "alive (kept as source)" in m for m in retry_lines)
    assert any("http://gone.example" in m and "still dead (dropped)" in m for m in retry_lines)
    # 复活的行随既有合并逻辑计回真实来源
    dfr._reset_fetched_sources()
    dfr._merge_pending_fetches(pending)
    merged = [s["url"] for s in dfr._FETCHED_SOURCES]
    assert "http://alive.example" in merged and "http://ok.example" in merged
    assert "http://gone.example" not in merged


def test_retry_disabled_by_env_zero(dfr, monkeypatch):
    monkeypatch.setenv("DEERFLOW_FETCH_RETRY", "0")
    monkeypatch.setattr(dfr, "_FETCH_RETRY_BACKOFF_S", 0.0)
    calls = []
    monkeypatch.setattr(dfr, "_retry_fetch_url",
                        lambda url, timeout=15.0: calls.append(url) or "x" * 300)
    pending = [{"url": "http://a.example", "call_id": "1", "ok": False}]
    dfr._retry_dead_fetches(pending, PlogStub())
    assert calls == []                               # 关闭 → 绝不发起重试
    assert pending[0]["ok"] is False


def test_retry_bounded_per_turn(dfr, monkeypatch):
    monkeypatch.delenv("DEERFLOW_FETCH_RETRY", raising=False)
    monkeypatch.setattr(dfr, "_FETCH_RETRY_BACKOFF_S", 0.0)
    calls = []
    monkeypatch.setattr(dfr, "_retry_fetch_url",
                        lambda url, timeout=15.0: calls.append(url) or "")
    pending = [{"url": f"http://u{i}.example", "call_id": str(i), "ok": False}
               for i in range(10)]
    dfr._retry_dead_fetches(pending, PlogStub())
    assert len(calls) == dfr._FETCH_RETRY_MAX_URLS   # 每回合有界（默认 6）


def test_retry_never_raises(dfr, monkeypatch):
    monkeypatch.delenv("DEERFLOW_FETCH_RETRY", raising=False)
    monkeypatch.setattr(dfr, "_FETCH_RETRY_BACKOFF_S", 0.0)
    monkeypatch.setattr(dfr, "_retry_fetch_url",
                        lambda url, timeout=15.0: (_ for _ in ()).throw(OSError("boom")))
    pending = [{"url": "http://a.example", "call_id": "1", "ok": False}]
    dfr._retry_dead_fetches(pending, None)           # plog=None 也不炸
    assert pending[0]["ok"] is False
