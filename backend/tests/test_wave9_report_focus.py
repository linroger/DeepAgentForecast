"""WAVE9 report-focus tests: 报告主语是现实世界，模拟只是内部方法。

Covers:
  * report_lint 纯函数：边转储改写 / 旧标签重写 / 孤悬归因清理 / 引用残留 & 记号规整 /
    重复整句去重 / 截断表格单元检测 / pass 叙述剥离 / 泄漏句删除 / lint_report 集成；
  * 反思护栏：裸 FAIL 跳过修订、反收缩护栏、截断检测 + 续写、动态章节字符下限；
  * 引文接地修复：归因行推演标签豁免 + 孤悬引子清理；
  * 泄漏修复 _repair_simulation_leakage（离线路径）与 _run_repair_passes 注册；
  * 信号包定性转写 salience_tiers_from_outcomes；大纲标题 lint；
  * render_resolution_block 语言参数 + 二元表 [:200] 截断移除。

全程离线（scripted fake LLM），与既有报告测试同一 __new__ + 属性注入 harness。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config  # noqa: E402
from app.services import report_lint as rl  # noqa: E402
from app.services.forecast_extractor import (  # noqa: E402
    render_binary_forecasts_block, render_resolution_block,
)
from app.services.report_agent import (  # noqa: E402
    ReportAgent, ReportOutline, ReportSection, _looks_truncated,
    salience_tiers_from_outcomes,
)


# ─────────────────────────── report_lint 纯函数 ───────────────────────────
def test_lint_edge_dump_rewritten_to_natural_language():
    md = "TSMC keeps pricing power (According to：ASML Holding N.V. --[SUPPLIES]--> TSMC).\n"
    out, converted, dangling = rl.rewrite_edge_dumps(md, "English")
    assert converted == 1 and dangling == 0
    assert "--[" not in out
    assert "supply relationship" in out
    assert "ASML Holding N.V." in out and "TSMC" in out


def test_lint_dangling_edge_intro_stripped():
    md = "The dependency is structural (According to：\n"
    out, converted, dangling = rl.rewrite_edge_dumps(md, "English")
    assert dangling == 1
    assert "According to" not in out


def test_lint_legacy_sim_labels_rewritten_en_and_zh():
    md = (
        "> Simulation Agent「Hyperscalers」Deduction/Reasoning: \"capex holds through 2027\"\n\n"
        "> 模拟代理人「台积电」推演：先进制程供不应求。\n"
    )
    out, n = rl.rewrite_sim_labels(md, "English")
    assert n == 2
    assert "Simulation Agent" not in out and "模拟代理人" not in out
    assert "Analytical perspective — Hyperscalers (scenario panel):" in out
    assert "情景推演专家视角——「台积电」：" in out


def test_lint_dangling_attribution_line_removed():
    md = (
        "Analytical perspective — TSMC (scenario panel) pushes back with evidence:\n\n"
        "## Next Section\n\nNormal prose continues here.\n"
    )
    out, n = rl.remove_dangling_attributions(md)
    assert n == 1
    assert "pushes back with evidence:" not in out
    assert "## Next Section" in out


def test_lint_dangling_attribution_keeps_legit_intro_before_quote():
    md = "The panel perspective notes:\n\n> \"a grounded quotation follows\"\n"
    out, n = rl.remove_dangling_attributions(md)
    assert n == 0 and out == md


def test_lint_citation_residue_and_variant_normalization():
    md = ("Growth hit $1.4T [citation:BofA $1.4T](https://rallies.ai/x) by 2030.\n"
          "Backed by 【S3】 and [S2-d] and [S1 / fact 8].\n")
    out, n_resid = rl.strip_citation_residue(md)
    assert n_resid == 1 and "[citation:" not in out
    out, n_var = rl.normalize_citation_variants(out)
    assert n_var == 3
    assert "[S3]" in out and "[S2]" in out and "[S1]" in out
    assert "【S3】" not in out and "[S2-d]" not in out


def test_lint_duplicate_sentence_dedup():
    dup = ("The equipment segment reproduces the same structural claim about its own "
           "architecture across the entire industry every single cycle.")
    md = f"{dup} Unique tail one.\n\nMiddle paragraph stays.\n\n{dup} Unique tail two.\n"
    out, removed = rl.dedup_duplicate_sentences(md)
    assert removed == 1
    assert out.count("equipment segment reproduces") == 1
    assert "Unique tail one." in out and "Unique tail two." in out


def test_lint_table_cell_truncation_detected():
    long_cut = "measured against the blended average selling price basket used in Q3 202" + "x" * 90
    md = f"| # | criteria |\n|---|---|\n| 1 | {long_cut} |\n"
    hits = rl.detect_table_cell_truncation(md)
    assert hits, "expected the truncated cell to be flagged"


def test_lint_pass_narration_stripped_and_flagged():
    md = ("Big Fund III ¥344B [Pass 2 working notes] anchored the estimate.\n"
          "Pass 4 of the broader dossier documented the contradiction.\n")
    out, stripped, flagged = rl.strip_pass_narration(md)
    assert stripped == 1
    assert "[Pass 2 working notes]" not in out
    assert flagged >= 1                       # 散文提及只标记不删


def test_lint_leakage_sentence_strip():
    text = ("TSMC retains pricing power through 2030. "
            "The simulation surfaced this claim in round 3 with 48 actions. "
            "Export controls remain the binding constraint [S2].")
    out, removed = rl.strip_leakage_sentences(text)
    assert removed == 1
    assert "round 3" not in out and "simulation" not in out
    assert "pricing power" in out and "[S2]" in out


def test_lint_platform_behavior_quote_dropped():
    md = "> The TSMC agent posted a thread and liked the post about HBM supply.\n\nProse.\n"
    out, n = rl.drop_platform_behavior_quotes(md)
    assert n == 1 and "posted a thread" not in out and "Prose." in out


def test_lint_report_end_to_end_fence_aware():
    md = (
        "# Title\n\n"
        "Claim (According to：BIS --[REGULATES]--> H100). [citation:x](https://y)\n\n"
        "```mermaid\nA --[SUPPLIES]--> B\n```\n\n"
        "> Simulation Agent「X」Deduction/Reasoning: \"kept\"\n"
    )
    out, rep = rl.lint_report(md, "English", mode="final")
    assert rep["changed"] is True
    assert "A --[SUPPLIES]--> B" in out          # 围栏内原样保留
    assert "(According to：BIS" not in out
    assert "[citation:" not in out
    assert "Analytical perspective — X (scenario panel):" in out
    assert rep["edge_dumps"] == 1 and rep["citation_residue"] == 1
    assert rep["legacy_sim_labels"] == 1


def test_lint_report_scenario_prob_crosscheck():
    spine = {"scenarios": [{"name": "Structural Supercycle", "probability": 0.45}]}
    md = "The Structural Supercycle scenario carries 62% odds in our view.\n"
    _, rep = rl.lint_report(md, "English", spine=spine)
    assert rep["scenario_prob_mismatches"], "expected a prose-vs-spine mismatch flag"


# ─────────────────────────── 反思护栏（section destroyer） ───────────────────
_LONG_DRAFT = ("This is a full-length analytical paragraph with mechanisms and evidence. " * 60)


class _ChatLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages=None, temperature=0.7, max_tokens=4096, tier="strong", **kw):
        self.calls.append({"messages": messages, "temperature": temperature,
                           "max_tokens": max_tokens, "tier": tier})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _reflect_agent(responses, **over):
    a = ReportAgent.__new__(ReportAgent)
    a.output_language = "English"
    a._forecast_spine = {"scenarios": [{"name": "Escalation", "probability": 0.6}]}
    a._forecast_spine_block = ""
    a._signal_pack = ""
    a.llm = _ChatLLM(responses)
    for k, v in over.items():
        setattr(a, k, v)
    return a


def _sec_outline():
    sec = ReportSection(title="Body 1", description="")
    return sec, ReportOutline(title="T", summary="S", sections=[sec])


def test_reflection_bare_fail_skips_revision():
    a = _reflect_agent(["FAIL"])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG_DRAFT, previous_sections=[])
    assert out == _LONG_DRAFT
    assert len(a.llm.calls) == 1              # 只有质检调用，没有修订调用


def test_reflection_rejects_shrinking_revision():
    stub = "A tiny stub replacement that would destroy the section. " * 30  # ~1.7KB but <60%
    assert len(stub) < 0.6 * len(_LONG_DRAFT)
    a = _reflect_agent(["Tighten the probability framing.", stub])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG_DRAFT, previous_sections=[])
    assert out == _LONG_DRAFT                 # 收缩修订被拒绝，保留原稿
    assert len(a.llm.calls) == 2


def test_reflection_accepts_good_revision():
    revised = "Revised with better probability framing. " + _LONG_DRAFT
    a = _reflect_agent(["Align probabilities.", revised])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, _LONG_DRAFT, previous_sections=[])
    assert out == revised.strip()


def test_truncation_detector_and_continuation(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_SECTION_TRUNCATION_CONTINUE", True, raising=False)
    truncated = _LONG_DRAFT + "\n\nSamsung's 2017-2018 cycle peaked at $46 and the (依据"
    assert _looks_truncated(truncated) is True
    assert _looks_truncated(_LONG_DRAFT) is False
    assert _looks_truncated("Ends with a list:\n- item one\n- item two") is False
    continuation = "billion capex round completed the argument. The section concludes cleanly."
    a = _reflect_agent(["PASS", continuation])
    sec, outline = _sec_outline()
    out = a._reflect_and_maybe_revise_section(sec, outline, truncated, previous_sections=[])
    assert "concludes cleanly." in out
    assert "(依据" not in out                  # 孤悬引子被剪掉
    assert len(a.llm.calls) == 2               # 质检 + 一次续写


def test_section_char_floor_scales_with_target(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_SECTION_MIN_VALID_RATIO", 0.4, raising=False)
    a = _reflect_agent(["PASS"])
    a.simulation_requirement = ""
    a.research_report = ""
    floor = a._section_char_floor()
    lo, _ = Config.report_section_target_chars()
    assert floor == max(800, int(lo * 0.4))


# ───────────────────── 引文接地：归因行标签豁免 + 孤悬引子清理 ─────────────────
def _repair_agent(**over):
    a = ReportAgent.__new__(ReportAgent)
    a.sources = []
    a.research_report = ""
    a.situation_brief = ""
    a._background_block = ""
    a._outline_summary = ""
    a.output_language = "English"
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_quote_grounding_respects_label_on_attribution_line():
    a = _repair_agent(research_report="irrelevant corpus")
    md = (
        "Analytical perspective — TSMC (scenario panel) argues:\n\n"
        "> \"Advanced packaging stays the binding constraint through 2028 at least.\"\n"
    )
    new_md, removed = a._repair_quote_grounding(md)
    assert removed == 0 and new_md == md      # 归因行带推演标签 → 引文豁免


def test_quote_grounding_removes_dangling_intro_with_quote():
    a = _repair_agent(research_report="the only grounded fact in this corpus")
    md = (
        "The market view pushes back hard on this claim:\n\n"
        "> \"A fabricated quotation that matches nothing in the research corpus at all.\"\n\n"
        "Prose continues after the deleted quote block.\n"
    )
    new_md, removed = a._repair_quote_grounding(md)
    assert removed == 1
    assert "fabricated quotation" not in new_md
    assert "pushes back hard on this claim:" not in new_md   # 孤悬引子同步清理
    assert "Prose continues" in new_md


# ───────────────────── 泄漏修复 + 修复链注册（离线路径） ─────────────────────
def test_repair_simulation_leakage_offline_deterministic():
    a = _repair_agent()                        # 无 llm → Tier-2 走句子级删除
    md = (
        "## 9. Agent Behavior in the Simulation\n\n"
        "TSMC holds pricing power [S1]. The simulation surfaced 48 actions in round 0. "
        "Export controls bind through 2027 [S2].\n\n"
        "> The TSMC agent posted a thread and liked the post repeatedly.\n"
    )
    new_md, info = a._repair_simulation_leakage(md)
    assert info["headings_renamed"] == 1
    assert "Agent Behavior" not in new_md
    assert "48 actions" not in new_md and "round 0" not in new_md
    assert "pricing power [S1]" in new_md and "[S2]" in new_md
    assert "posted a thread" not in new_md     # 平台行为引文被删


def test_run_repair_passes_triggers_simleak(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_SIMLEAK_REPAIR", True, raising=False)
    monkeypatch.setattr(Config, "REPORT_PUBLISH_GATE_MIN_COVERAGE", 0.0, raising=False)
    a = _repair_agent()
    md = "Solid claim [S1]. The simulation surfaced this in round 2 with 30 actions.\n"
    forecast = {"citation_audit": {"coverage": 1.0, "quantitative_claims": 0}, "quality": {}}
    new_md = a._run_repair_passes("rid_sl", forecast, md, report=None)
    assert "round 2" not in new_md
    rep = forecast["quality"]["repair"]
    assert any(p["dimension"] == "simulation_leakage" for p in rep["passes"])
    assert rep["before"]["sim_leakage_hits"] > rep["after"]["sim_leakage_hits"]
    assert forecast["quality"]["sim_leakage"]["after"] < forecast["quality"]["sim_leakage"]["before"]


# ───────────────────── 信号包定性转写 + 大纲标题 lint ─────────────────────
def test_salience_tiers_from_outcomes():
    outcomes = (
        "## 模拟量化结果（结构化，可直接引用）\n\n### 最活跃 Agent（Top 3，按总动作数）\n"
        "- TSMC(id=1): 共 48 次动作 [CREATE_POST×21]\n"
        "- NVIDIA(id=2): 共 40 次动作 [CREATE_COMMENT×12]\n"
        "- Micron(id=3): 共 5 次动作 [LIKE_POST×5]\n"
    )
    tiers = salience_tiers_from_outcomes(outcomes)
    assert "第一梯队" in tiers and "TSMC" in tiers and "NVIDIA" in tiers
    assert "第三梯队" in tiers and "Micron" in tiers
    assert "次动作" not in tiers and "48" not in tiers   # 机制数字不进注入文本
    assert salience_tiers_from_outcomes("（无数据）") == ""


def test_outline_title_lint_renames_leaky_titles():
    a = ReportAgent.__new__(ReportAgent)
    a.output_language = "English"
    sections = [
        ReportSection(title="Agent Behavior in the Simulation"),
        ReportSection(title="Power Transition Scenarios"),
        ReportSection(title="模拟证据与行为轨迹"),
    ]
    renamed = a._lint_outline_titles(sections)
    assert renamed == 2
    assert len(sections) == 3                  # 改名而非删除（章节数契约）
    titles = [s.title for s in sections]
    assert "Power Transition Scenarios" in titles
    for t in titles:
        assert not ReportAgent._LEAK_TITLE_RE.search(t), t


# ───────────────────── 判定章节语言 + 二元表截断移除 ─────────────────────
def test_render_resolution_block_english():
    fc = {"scenarios": [{"name": "Base case", "probability": 0.6,
                         "resolution_criteria": "Revenue >= $1T by 2030 per Gartner."}]}
    block = render_resolution_block(fc, [{"indicator": "HBM share", "date": "2027-01",
                                          "scenario": "Base case"}], language="English")
    assert "How to Verify This Forecast" in block
    assert "如何验证本预测" not in block
    assert "| Indicator | Due / trigger |" in block
    # 默认（中文）行为不变
    zh_block = render_resolution_block(fc)
    assert "如何验证本预测" in zh_block


def test_render_binary_forecasts_block_no_criteria_truncation():
    crit = ("Resolves YES if industry revenue measured against the blended basket used in "
            "Q3 2029 exceeds $1.6T per the arithmetic mean of Gartner and IDC full-year "
            "prints published before 2031-03-31, using vendor-reported segment definitions.")
    assert len(crit) > 200
    fc = {"binary_forecasts": [{"id": "F1", "statement": "Revenue ≥ $1.6T", "probability": 0.62,
                                "resolution_criteria": crit, "theme": "market"}]}
    block = render_binary_forecasts_block(fc, language="English")
    assert crit in block                       # 不再被 [:200] 截断
