"""SMOKE 实跑回归：report_a03be154febc 真实成稿上发现的泄漏残留类别。

对完整真实成稿跑确定性 lint + 模拟泄漏修复（无 LLM）后仍有 34 处命中，全部集中在
非散文结构里：表格行（`|` 开头）、加粗导语段（`**` 开头——被段落扫描器按列表/强调
跳过）。这些残留必须至少被 leakage_hits 计入 flag 遥测——本文件把这几个真实形状
钉成回归守卫，同时锁定实跑中的多边 edge-dump 变体确实被确定性改写清零。
"""

from app.services import report_lint as rl
from app.services.report_agent import ReportAgent


def _agent(lang: str = "English") -> ReportAgent:
    a = ReportAgent.__new__(ReportAgent)
    a.output_language = lang
    return a


# ── 真实成稿形状 1：多边、全角分号分隔的 edge dump（full_report.md:421 同款） ──

def test_real_run_multi_edge_dump_rewritten_clean():
    md = ("ASML remains the sole EUV supplier "
          "(According to：ASML Holding N.V. --[SUPPLIES]--> TSMC；"
          "ASML Holding N.V. --[SUPPLIES]--> Samsung Electronics；"
          "ASML Holding N.V. --[SUPPLIES]--> Intel Corporation).")
    out, n_dumps, _dangling = rl.rewrite_edge_dumps(md, "English")
    assert "--[" not in out
    assert n_dumps >= 1                      # 计数按转储块计，非按边计
    # 关系语义保留为自然语言，而不是整句蒸发
    assert "TSMC" in out and "ASML" in out


# ── 真实成稿形状 2：表格行内的 Simulation Agent 标签（linted 后残留 6 处的形状） ──

_TABLE_ROW = ("| Simulation Agent「sk_hynix_989」 | 15–20% 中段 | <10% | "
              "防守方（夸大压力以维持对 HBM 投入正当性） | [S2] |")


def test_table_row_sim_label_is_flagged_by_leakage_hits():
    hits = rl.leakage_hits(_TABLE_ROW)
    assert any(h == "simulation_agent_label" for h in hits), (
        "表格行里的 Simulation Agent 标签必须被 leakage_hits 计入 flag 遥测")


def test_table_row_sim_label_survives_deterministic_repair_but_stays_flagged():
    md = "## Section\n\nProse line.\n\n" + _TABLE_ROW + "\n\nMore prose.\n"
    out, info = _agent()._repair_simulation_leakage(md)
    # 确定性层按设计不动表格结构（避免破坏表格），但 lint 报告必须继续 flag 它
    _, rep = rl.lint_report(out, "English", mode="final")
    if "Simulation Agent「" in out:
        assert rep["leakage_flags"] >= 1
    # 无论走哪条路径，修复过程不得让文档丢失表格本体
    assert out.count("|") >= _TABLE_ROW.count("|") - 2


# ── 真实成稿形状 3：加粗导语的机制叙述段（linted 后 round_n/动作词残留的来源） ──

_BOLD_LEAD_PARA = ("**第二阶段“定位期”为 round 10–11**：54 与 39 次动作，"
                   "活跃 Agent 扩展到 14 和 12 个。此阶段主导动作从 CREATE_POST "
                   "转向 CREATE_COMMENT。")


def test_bold_lead_mechanics_paragraph_is_flagged():
    hits = rl.leakage_hits(_BOLD_LEAD_PARA)
    assert "round_n" in hits
    assert any(h == "action_type_tokens" for h in hits)


def test_bold_lead_paragraph_repair_never_crashes_and_flags_survive():
    md = "## 第九章\n\n" + _BOLD_LEAD_PARA + "\n\n正文继续。\n"
    out, info = _agent("Chinese")._repair_simulation_leakage(md)
    assert isinstance(info, dict)
    # 若确定性层没能清除（当前实现按 '*' 前缀跳过该段），flag 遥测必须仍然命中，
    # 保证残留在 quality['sim_leakage'] 里可观测而不是静默通过。
    if "CREATE_POST" in out or "round 10" in out:
        _, rep = rl.lint_report(out, "Chinese", mode="final")
        assert rep["leakage_flags"] >= 1
