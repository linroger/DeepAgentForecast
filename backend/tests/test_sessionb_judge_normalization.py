"""SESSION-B：judge 记分牌确定性归一（_normalize_judge_scorecard）回归测试。

取证背景（pipe_bef6879b2e94，$100 深度证据 run）：两次全局综合各产出 17K-23K 词的
报告，judge 打 length_vs_target=3 + verdict FAIL。长度分可以由确定性字数计数纠正；
但是后续真实回放证明显式 FAIL 还可能编码章节截断、枚举未完成、引用标记残缺和只有
图表规格没有真实数据等数值维度未表达的缺陷。修复：在记分牌出生点
（judge_research_report → _normalize_judge_scorecard）只做一条确定性归一：

1. 长度客观可测：count_prose_words(report) ≥ 判长下限（deep 15000 / 其余 3500）而
   judge 打分 <4 → 修正为 4，并写 length_override 溯源块。
2. verdict 永不被数值反向覆盖。显式 FAIL 保持权威，即使长度修正后全部数值越线。

对抗性不变量（本文件同时封印）：长度归一**只升长度维到 4、绝不降分、绝不越过其他
维**——20K 词也洗不白 citation_coverage=2；verdict=PASS 但数值不达标的记分牌归一后
两侧闸门（桥 report_passes / 编排器 _research_judge_passes）依旧拒绝；两侧闸门对同
一份已归一记分牌的判定必须逐字一致（闸门口径奇偶校验）。

与 test_bridge_search_and_cache.py 同构：把 deerflow_bridge 挂上 sys.path 直接
import（deerflow_research 的 deerflow/langchain 重依赖均为函数内惰性 import，
纯逻辑测试零网络、零 LLM）。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE_DIR = _REPO_ROOT / "deerflow_bridge"
if str(_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_DIR))

import deerflow_research as dr  # noqa: E402
from app.services import pipeline_orchestrator as po  # noqa: E402

# pipe_bef6879b2e94 的真实取证归档（两次失败的全局综合 + 各自的精修候选稿）。
_ATTEMPTS_DIR = (
    _REPO_ROOT / "backend" / "uploads" / "pipelines" / "pipe_bef6879b2e94"
    / "handoff" / "research_attempts"
)
_ATTEMPT_1 = "global_synthesis_1_failed_59df53313c18"
_ATTEMPT_2 = "global_synthesis_2_failed_b8ef76e0d969"


class _Log:
    """ProgressLog 替身：只记录 (kind, message)，供断言溯源日志确实写出。"""

    def __init__(self):
        self.rows = []

    def write(self, kind, message):
        self.rows.append((kind, str(message)))


def _scorecard(verdict: str, **overrides) -> dict:
    """按 _REPORT_JUDGE_DIMS 顺序构造七维记分牌；未覆写的维默认 4。"""
    scores = dict.fromkeys(dr._REPORT_JUDGE_DIMS, 4)
    for dim, value in overrides.items():
        assert dim in dr._REPORT_JUDGE_DIMS, f"未知维度 {dim}"
        scores[dim] = value
    return {"scores": scores, "verdict": verdict, "gaps": []}


def _report_of_words(n: int) -> str:
    """恰好 n 个散文词的报告体（纯 ASCII 词，无表格/URL/围栏干扰计数）。"""
    text = "insight " * n
    assert dr.count_prose_words(text) == n
    return text


@pytest.fixture(autouse=True)
def _clean_strict_flag(monkeypatch):
    """默认非 STRICT；strict 用例各自显式 setenv（避免外部环境串扰口径）。"""
    monkeypatch.delenv("RESEARCH_REPORT_JUDGE_STRICT", raising=False)


# ================================================================ (a) 长度确定性修正

def test_length_corrected_at_deep_floor_with_provenance():
    sc = _scorecard("PASS", length_vs_target=3)
    plog = _Log()
    out = dr._normalize_judge_scorecard(sc, _report_of_words(15000), "deep", plog)

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["length_override"] == {
        "measured_prose_words": 15000,
        "floor_words": 15000,
        "judge_score": 3.0,
    }
    assert any("length_vs_target deterministically corrected" in m
               for _k, m in plog.rows)


def test_length_corrected_at_standard_floor():
    sc = _scorecard("PASS", length_vs_target=2)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(3500), "standard", _Log())

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["length_override"]["floor_words"] == 3500
    assert out["length_override"]["judge_score"] == 2.0


def test_length_not_corrected_below_floor():
    for depth, below in (("deep", 14999), ("standard", 3499)):
        sc = _scorecard("PASS", length_vs_target=3)
        plog = _Log()
        out = dr._normalize_judge_scorecard(sc, _report_of_words(below), depth, plog)

        assert out["scores"]["length_vs_target"] == 3, depth
        assert "length_override" not in out, depth
        assert plog.rows == [], depth


def test_length_never_lowered_when_judge_already_scored_high():
    # 归一只升不降：judge 给 5 的长度维不被压回 4，也不写 override 溯源。
    sc = _scorecard("PASS", length_vs_target=5)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(20000), "deep", _Log())

    assert out["scores"]["length_vs_target"] == 5
    assert "length_override" not in out


# ================================================================ (b) 显式 FAIL 保持权威

def test_fail_preserved_when_all_numeric_bars_clear_after_length_correction():
    # 数值维度不能表达章节截断/残缺引用等离散缺陷，不能覆盖显式 FAIL。
    sc = _scorecard("FAIL", quantitative_density=5, length_vs_target=3)
    plog = _Log()
    out = dr._normalize_judge_scorecard(sc, _report_of_words(17000), "deep", plog)

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in out
    assert dr.report_passes(out) is False
    assert not any("verdict FAIL reconciled to PASS" in m for _k, m in plog.rows)


def test_fail_preserved_when_mean_bar_genuinely_fails():
    # attempt-2 原判的精确形状：base_rate_usage=3 把修正后均分拖到 27/7 < 4。
    sc = _scorecard("FAIL", base_rate_usage=3, length_vs_target=3)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(17911), "deep", _Log())

    assert out["scores"]["length_vs_target"] == 4.0  # 长度照常修正
    assert out["verdict"] == "FAIL"                   # 但 FAIL 保持权威
    assert "verdict_reconciled_from_fail" not in out
    assert dr.report_passes(out) is False


def test_fail_preserved_when_critical_dim_at_three():
    # critical 维（mechanism_chains）=3：均分 29/7 ≥ 4 也不放行。
    sc = _scorecard("FAIL", mechanism_chains=3, quantitative_density=5,
                    citation_coverage=5, length_vs_target=3)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(16000), "deep", _Log())

    assert out["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in out
    assert dr.report_passes(out) is False


# ================================================================ (c) 长度洗不白质量

def test_garbage_guard_20k_words_cannot_launder_citation_coverage():
    sc = _scorecard("FAIL", thesis_specificity=5, base_rate_usage=5,
                    mechanism_chains=5, quantitative_density=5,
                    contrarian_coverage=5, length_vs_target=3,
                    citation_coverage=2)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(20000), "deep", _Log())

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["scores"]["citation_coverage"] == 2  # 其余维一字不动
    assert out["verdict"] == "FAIL"
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False


# ================================================================ (d) 畸形记分牌 pass-through

@pytest.mark.parametrize("malformed", [
    {"scores": {d: 4 for d in dr._REPORT_JUDGE_DIMS if d != "citation_coverage"},
     "verdict": "FAIL"},                                     # 缺维
    {"scores": dict.fromkeys(dr._REPORT_JUDGE_DIMS, "4"), "verdict": "FAIL"},  # 字符串分
    {"scores": dict.fromkeys(dr._REPORT_JUDGE_DIMS, 4), "verdict": "MAYBE"},   # 非法 verdict
    {"scores": [4] * 7, "verdict": "FAIL"},                  # scores 非 dict
    {"scores": dict.fromkeys(dr._REPORT_JUDGE_DIMS, 4), "verdict": "FAIL",
     "_judge_input": {"truncated": True}},                   # 截断输入永不归一
])
def test_malformed_scorecard_passes_through_unchanged(malformed):
    snapshot = copy.deepcopy(malformed)
    plog = _Log()
    out = dr._normalize_judge_scorecard(malformed, _report_of_words(20000), "deep", plog)

    assert out is malformed          # 同一对象原样返回
    assert out == snapshot           # 未新增/修改任何键
    assert plog.rows == []           # 未写任何归一日志
    assert dr.report_passes(out) is False


# ================================================================ (e) STRICT 模式一致性

def test_explicit_fail_binds_in_relaxed_and_strict_modes(monkeypatch):
    # 非 critical 维（base_rate_usage）=3、均分 32/7≈4.57，显式 FAIL 在两种模式均绑定。
    sc = _scorecard("FAIL", thesis_specificity=5, base_rate_usage=3,
                    mechanism_chains=5, quantitative_density=5,
                    contrarian_coverage=5, length_vs_target=3,
                    citation_coverage=5)
    relaxed = dr._normalize_judge_scorecard(
        copy.deepcopy(sc), _report_of_words(16000), "deep", _Log())
    assert relaxed["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in relaxed
    assert dr.report_passes(relaxed) is False

    # STRICT 继续同拒，且两侧闸门口径一致。
    monkeypatch.setenv("RESEARCH_REPORT_JUDGE_STRICT", "true")
    strict = dr._normalize_judge_scorecard(
        copy.deepcopy(sc), _report_of_words(16000), "deep", _Log())
    assert strict["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in strict
    assert dr.report_passes(strict) is False
    assert po._research_judge_passes(strict) is False


# ================================================================ 对抗性不变量

def test_pass_verdict_with_failing_numerics_is_not_blessed_by_normalization():
    # 归一只修长度；verdict=PASS 但数值不达标的记分牌
    # 归一后仍被两侧闸门拒绝（修复未为既有坏状态开口子）。
    sc = _scorecard("PASS", citation_coverage=2, length_vs_target=3)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(20000), "deep", _Log())

    assert out["verdict"] == "PASS"  # 归一不伪造 FAIL……
    assert dr.report_passes(out) is False       # ……但闸门照拒
    assert po._research_judge_passes(out) is False


def test_pass_verdict_cannot_launder_its_own_structural_failure_gap():
    # pipe_0e1b84d2682a 的真实矛盾形状：verdict=PASS / 数值全过线，但 gaps 明说
    # 场景节在句中截断，承诺的 10–12 个预测未交付。两侧门都必须保守拒绝。
    sc = _scorecard("PASS", quantitative_density=5, citation_coverage=5)
    sc["gaps"] = [
        "Scenario section appears truncated mid-sentence, so several promised "
        "sub-deliverables are not delivered",
    ]
    out = dr._normalize_judge_scorecard(
        sc, _report_of_words(19599), "deep", _Log())

    assert out["verdict"] == "PASS"
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False


def test_pass_verdict_may_keep_nonfatal_improvement_notes():
    sc = _scorecard("PASS")
    sc["gaps"] = [
        "Add another historical analogue for India tender conversion if space permits",
    ]

    assert dr.report_passes(sc) is True
    assert po._research_judge_passes(sc) is True


@pytest.mark.parametrize("overrides,verdict,words", [
    ({"length_vs_target": 3, "quantitative_density": 5}, "FAIL", 17000),
    ({"length_vs_target": 3, "base_rate_usage": 3}, "FAIL", 17911),
    ({"citation_coverage": 2, "length_vs_target": 3}, "FAIL", 20000),
    ({"length_vs_target": 3}, "PASS", 14000),
    ({}, "PASS", 16000),
    ({"mechanism_chains": 3, "citation_coverage": 5,
      "quantitative_density": 5, "length_vs_target": 3}, "FAIL", 16000),
])
def test_bridge_and_orchestrator_gates_agree_on_normalized_scorecards(
        overrides, verdict, words):
    # 闸门口径奇偶校验：桥 report_passes 与编排器 _research_judge_passes 消费同一份
    # 已归一记分牌时判定必须逐字一致（归一发生在出生点正是为了这个性质）。
    sc = _scorecard(verdict, **overrides)
    out = dr._normalize_judge_scorecard(sc, _report_of_words(words), "deep", _Log())

    assert dr.report_passes(out) is po._research_judge_passes(out)


# ================================================================ (f) 真实归档回放

def _load_attempt(stem: str, kind: str) -> "tuple[str, dict]":
    """读归档报告 + 记分牌，并按当前报告字节重绑 _judge_input 身份块。"""
    report = (_ATTEMPTS_DIR / f"{stem}.{kind}.md").read_text(encoding="utf-8")
    sc = json.loads(
        (_ATTEMPTS_DIR / f"{stem}.{kind}_judge.json").read_text(encoding="utf-8"))
    _bounded, identity = dr._report_judge_input(report)
    assert identity["truncated"] is False
    sc["_judge_input"] = identity
    return report, sc


@pytest.mark.skipif(not _ATTEMPTS_DIR.is_dir(),
                    reason="pipe_bef6879b2e94 取证归档不在本机")
def test_replay_attempt_1_archived_scorecard_remains_fail():
    report, sc = _load_attempt(_ATTEMPT_1, "research_report")
    assert sc["verdict"] == "FAIL"                       # 归档原判
    assert sc["scores"]["length_vs_target"] == 3
    assert dr.count_prose_words(report) >= dr._judge_length_floor_words("deep")

    plog = _Log()
    out = dr._normalize_judge_scorecard(sc, report, "deep", plog)

    assert out["length_override"]["floor_words"] == 15000
    assert out["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in out
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False


@pytest.mark.skipif(not _ATTEMPTS_DIR.is_dir(),
                    reason="pipe_bef6879b2e94 取证归档不在本机")
def test_replay_attempt_2_original_and_refinement_stay_fail():
    # attempt-2 原判：base_rate_usage=3 → 修正后均分 27/7 < 4，FAIL 保持权威。
    report, sc = _load_attempt(_ATTEMPT_2, "research_report")
    assert sc["scores"]["base_rate_usage"] == 3
    out = dr._normalize_judge_scorecard(sc, report, "deep", _Log())

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["verdict"] == "FAIL"
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False

    # 精修候选仍是 judge 明确 FAIL；数值越线不能抹掉未编码的结构性缺陷。
    cand_report, cand_sc = _load_attempt(
        _ATTEMPT_2, "research_report_refinement_candidate")
    cand_out = dr._normalize_judge_scorecard(cand_sc, cand_report, "deep", _Log())

    assert cand_out["verdict"] == "FAIL"
    assert "verdict_reconciled_from_fail" not in cand_out
    assert dr.report_passes(cand_out) is False
    assert po._research_judge_passes(cand_out) is False


def test_latest_run_substantive_fail_survives_length_correction():
    """pipe_750d99882585 attempt 2: numeric scores looked passable after the
    objective length correction, but the judge identified genuinely truncated
    M9/F12 output and incomplete actual-data deliverables. Those discrete gaps
    MUST remain publication-blocking.
    """
    sc = _scorecard("FAIL", quantitative_density=5, length_vs_target=3)
    sc["gaps"] = [
        "M9 is truncated mid-sentence",
        "F12 ends with an incomplete [S49 citation marker",
        "visualization section contains specifications but no actual-data tables",
    ]

    out = dr._normalize_judge_scorecard(
        sc, _report_of_words(19437), "deep", _Log())

    assert out["scores"]["length_vs_target"] == 4.0
    assert out["verdict"] == "FAIL"
    assert out["gaps"] == sc["gaps"]
    assert "verdict_reconciled_from_fail" not in out
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False


_SCENARIO_CONFLICT_REPORT = """
## Scenarios (4 mutually exclusive, summing to 100%)
### SCN-A — Lithium Dominance: 30%
### SCN-B — LDES Diversified: 25%
### SCN-C — China-Centric Vertical: 20%
### SCN-D — Stagnation: 25%

## Binary Forecasts and Visualization Specifications
#### V7. Four-Scenario Probability Split
| Scenario | Resolution axis | Probability |
|---|---|---:|
| A. High growth, lithium-dominant | yes/no | 47% |
| B. High growth, diversified | yes/yes | 29% |
| C. Low growth, lithium-dominant | no/no | 18% |
| D. Low growth, LDES leap | no/yes | 6% |
"""


def test_deterministic_gate_rejects_conflicting_repeated_scenario_table():
    conflicts = dr.scenario_probability_conflicts(_SCENARIO_CONFLICT_REPORT)
    assert {row["scenario_key"] for row in conflicts} == {"A", "B", "C", "D"}

    sc = _scorecard("PASS")
    out = dr._normalize_judge_scorecard(
        sc, _SCENARIO_CONFLICT_REPORT, "standard", _Log())

    assert out["verdict"] == "FAIL"
    assert out["scenario_probability_conflicts"] == conflicts
    assert dr.report_passes(out) is False
    assert po._research_judge_passes(out) is False


def test_deterministic_gate_accepts_exact_scenario_restatement():
    matching = _SCENARIO_CONFLICT_REPORT.replace(
        "47%", "30%"
    ).replace("29%", "25%").replace("18%", "20%").replace("6%", "25%")
    assert dr.scenario_probability_conflicts(matching) == []


def test_report_judge_prompt_names_scenario_consistency_hard_gate():
    prompt = dr.build_report_judge_prompt("Forecast X", "English", "15,000 words")
    assert "情景一致性硬门" in prompt
    assert "互相矛盾但各自合计 100%" in prompt
