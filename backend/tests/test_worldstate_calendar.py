"""日历改造（temporal spec §4/§7-4）：WorldState 熵地板 + 决策通道日历轨迹（offline，fake LLM）。

Pins: lam = min(0.05, 0.0005×天数)（day→0.0005，half_year→0.05 封顶）；回混目标是
**种子先验**而非均匀分布（偏斜先验方向保持）；``entropy_mix_days=None`` 与旧行为
逐字节一致；``_inertia_for_gap`` 吃 snap 后不等长时段的真实 gap；收敛只是稳定性
信号——回放从不早停，轨迹始终推演到最后一轮。
"""

from app.services.decision_channel import (
    _build_round_decision_prompt,
    _inertia_for_gap,
    elicit_round,
    run_decision_channel,
)
from app.services.worldstate import WorldState
from tests.conftest import FakeLLMClient


# ------------------------------------------------------------------ 熵地板 lam pin
def test_entropy_lam_pinned_one_day():
    """day（1 天）→ lam = 0.0005，逐字节 pin（spec §4 数值一致性修复）。"""
    ws = WorldState(["A", "B"], base_rates={"A": 0.7, "B": 0.3})
    ws.shares = {"A": 0.5, "B": 0.5}
    ws.step([], entropy_mix_days=1)
    # 0.9995×0.5 + 0.0005×seed → A=0.5001, B=0.4999
    assert ws.shares == {"A": 0.5001, "B": 0.4999}


def test_entropy_lam_capped_at_half_year():
    """half_year（182 天）→ lam 封顶 0.05；更长（365 天）不再增大。"""
    for days in (100, 182, 365):  # 100 天恰好触顶（0.0005×100 = 0.05）
        ws = WorldState(["A", "B"], base_rates={"A": 0.7, "B": 0.3})
        ws.shares = {"A": 0.5, "B": 0.5}
        ws.step([], entropy_mix_days=days)
        assert ws.shares == {"A": 0.51, "B": 0.49}, f"days={days}"


def test_entropy_mixes_toward_seed_prior_not_uniform():
    """回混目标是种子先验（90/10），不是均匀分布——偏斜先验方向被保持。"""
    commitments = [{"scenario": "B", "magnitude": 1.0, "weight": 4.0},
                   {"scenario": "A", "magnitude": 1.0, "weight": 1.0}]
    mixed = WorldState(["A", "B"], base_rates={"A": 0.9, "B": 0.1}, inertia=0.0)
    mixed.step(commitments, entropy_mix_days=100)          # lam = 0.05
    plain = WorldState(["A", "B"], base_rates={"A": 0.9, "B": 0.1}, inertia=0.0)
    plain.step(commitments)                                # 无熵地板 → A=0.2
    # 0.95×0.2 + 0.05×0.9 = 0.235（向先验回拉）；向均匀回混只会给 0.215
    assert mixed.shares == {"A": 0.235, "B": 0.765}
    assert plain.shares == {"A": 0.2, "B": 0.8}
    assert mixed.shares["A"] > 0.215 > plain.shares["A"]


def test_entropy_none_is_byte_identical_legacy():
    """entropy_mix_days=None（所有旧调用方）→ 份额与历史逐字节不变。"""
    seq = [[{"scenario": "A", "magnitude": 1.0, "weight": 2.0}],
           [],
           [{"scenario": "B", "magnitude": 0.5, "weight": 1.0}]]
    legacy = WorldState(["A", "B"], base_rates={"A": 0.6, "B": 0.4}, inertia=0.7)
    explicit = WorldState(["A", "B"], base_rates={"A": 0.6, "B": 0.4}, inertia=0.7)
    for cs in seq:
        legacy.step(cs)
        explicit.step(cs, entropy_mix_days=None)
    assert legacy.shares == explicit.shares
    assert legacy.history == explicit.history
    assert legacy.outcome() == explicit.outcome()
    # 旧公式数值 pin：0.7×0.6+0.3=0.72；空轮不动；再 0.7×0.72=0.504
    assert legacy.history[1] == {"A": 0.72, "B": 0.28}
    assert legacy.history[2] == {"A": 0.72, "B": 0.28}
    assert legacy.shares == {"A": 0.504, "B": 0.496}


# ------------------------------------------- _inertia_for_gap × snap 后不等长时段
def test_inertia_for_gap_variable_snapped_period_lengths():
    """日历时段 snap 到自然边界后首/尾段不等长：gap 越短 inertia 越高（放行越少）。
    avg_gap = 季度名义天数 91.31（spec §4）。"""
    base, avg = 0.7, 91.31
    short = _inertia_for_gap(base, "2026-07-11", "2026-09-30", avg)   # 81 天首残段
    full = _inertia_for_gap(base, "2026-09-30", "2026-12-31", avg)    # 92 天整季
    merged = _inertia_for_gap(base, "2026-12-31", "2027-05-15", avg)  # 135 天尾段合并
    assert short > full > merged
    assert abs(full - base ** (92 / avg)) < 1e-9
    assert abs(short - base ** (81 / avg)) < 1e-9
    assert abs(merged - base ** (135 / avg)) < 1e-9
    assert 0.3 <= merged and short <= 0.95                            # 钳制区间内
    # 日期缺失 / 负 gap / 退化 base → None（调用方回退到 base），与旧行为一致
    assert _inertia_for_gap(base, None, "2026-09-30", avg) is None
    assert _inertia_for_gap(base, "2026-09-30", "2026-06-30", avg) is None
    assert _inertia_for_gap(1.0, "2026-09-30", "2026-12-31", avg) is None


# ----------------------------------------------------------------- 从不早停
def test_convergence_never_early_stops_replay():
    """收敛（converged_at）只是稳定性信号：即便早早收敛，回放也步进每一轮到底。"""
    rounds = 25
    actions = [{"round": r, "agent_id": 1} for r in range(1, rounds + 1)]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "A", "magnitude": 1, "confidence": 1}]}])
    res = run_decision_channel(
        actions, [{"agent_id": 1, "influence_weight": 1.0}],
        {"scenarios": ["A", "B"], "base_rates": {"A": 0.5, "B": 0.5}}, fake, inertia=0.7)
    assert res["converged_at"] is not None and res["converged_at"] <= 20  # 早已收敛…
    assert res["n_rounds"] == rounds                                      # …但仍推演到底
    assert len(res["trajectory"]) == rounds + 1
    assert res["trajectory"][-1]["round"] == rounds
    # hours/legacy 路径输出形状不变：v2、无日历字段、decisions 行无 period_end/weight
    assert res["schema_version"] == 2 and "mode" not in res
    assert all("period_end" not in d and "weight" not in d for d in res["decisions"])


# ------------------------------------------------------------- 日历路径（round_dates）
_ROUND_DATES = [
    {"round": 0, "period_start": "2026-07-12", "period_end": "2026-09-30", "label": "2026-Q3"},
    {"round": 1, "period_start": "2026-10-01", "period_end": "2026-12-31", "label": "2026-Q4"},
    {"round": 2, "period_start": "2027-01-01", "period_end": "2027-03-31", "label": "2027-Q1"},
]


def test_run_decision_channel_calendar_round_dates():
    actions = [{"round": r, "agent_id": 1} for r in (1, 2, 3)]
    fake = FakeLLMClient(json_responses=[
        {"decisions": [{"agent_id": 1, "scenario": "A", "magnitude": 1, "confidence": 1}]}] * 3)
    seed = {"scenarios": ["A", "B"], "base_rates": {"A": 0.5, "B": 0.5},
            "as_of_date": "2026-07-11", "horizon_date": "2027-03-31",
            "horizon_source": "explicit_date", "horizon_defaulted": False}
    res = run_decision_channel(actions, [{"agent_id": 1, "influence_weight": 1.0}],
                               seed, fake, round_dates=_ROUND_DATES)
    # 缓存键含时段 label：同一 roster 不再跨轮合并 → 每个时段各一次调用
    assert len(fake.calls) == 3
    # schema v3 + 日历顶层字段（spec §6）
    assert res["schema_version"] == 3 and res["mode"] == "calendar"
    assert res["calendar_unit"] == "quarter"
    assert res["horizon_date"] == "2027-03-31"
    assert res["horizon_source"] == "explicit_date" and res["horizon_defaulted"] is False
    # decisions 行带 period_end（spec §4）
    assert [d["period_end"] for d in res["decisions"]] == [
        "2026-09-30", "2026-12-31", "2027-03-31"]
    # 轨迹行带日期；第 0 行 as_of = as_of_date（spec §6）
    assert res["trajectory"][0]["as_of"] == "2026-07-11"
    snap1 = res["trajectory"][1]
    assert snap1["as_of"] == snap1["period_end"] == "2026-09-30"
    assert snap1["period_start"] == "2026-07-12" and snap1["label"] == "2026-Q3"
    assert res["outcome"]["leader"] == "A" and res["outcome"]["shares"]["A"] > 0.5
    # 提示词切为时段框架（spec §5 verbatim），基线锚定保留、旧轮次框架不再出现
    prompts = [c["messages"][0]["content"] for c in fake.calls]
    q3 = next(p for p in prompts if "2026-09-30" in p)
    assert "时段：第 1/3 轮，覆盖 2026-07-12 至 2026-09-30（一个季度）。" in q3
    assert "距离判定日 2027-03-31 还有 182 天。" in q3
    assert "请给出你的行动体在这一整个时段内的实际投入方向与行动承诺。" in q3
    assert "当前建模基线分布" in q3 and "A=50%" in q3     # 基线（种子先验）锚定保留
    assert "（对应时点约" not in q3                        # 旧框架被替换
    q1 = next(p for p in prompts if "覆盖 2027-01-01 至 2027-03-31" in p)
    assert "距离判定日 2027-03-31 还有 0 天。" in q1       # 最后一段：0 天


def test_elicit_round_returns_step_ready_commitments():
    """elicit_round 是 in-band / post-hoc 共用核心：返回可直接喂 WorldState.step 的
    commitments（weight = outcome_power×confidence），弃权被过滤，带审计字段。"""
    roster = [{"agent_id": 1, "name": "甲", "stance": "pro", "outcome_power": 4.0},
              {"agent_id": 2, "name": "乙", "stance": "con", "outcome_power": 1.0}]
    fake = FakeLLMClient(json_responses=[{"decisions": [
        {"agent_id": 1, "scenario": "S1", "magnitude": 1.0, "confidence": 0.5},
        {"agent_id": 2, "scenario": "弃权", "magnitude": 1.0, "confidence": 1.0}]}])
    ctx = {"llm": fake, "scenarios": ["S1", "S2"], "round_num": 2, "n_rounds": 4,
           "base_shares": {"S1": 0.5, "S2": 0.5}, "horizon_date": "2026-12-31",
           "unit": "quarter",
           "period": {"period_start": "2026-10-01", "period_end": "2026-12-31",
                      "label": "2026-Q4"}}
    out = elicit_round(roster, ctx)
    assert len(out) == 1                                   # 弃权 → 不计入
    c = out[0]
    assert c["scenario"] == "S1" and c["round"] == 2
    assert c["outcome_power"] == 4.0 and c["weight"] == 4.0 * 0.5
    assert c["period_end"] == "2026-12-31"
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "时段：第 2/4 轮，覆盖 2026-10-01 至 2026-12-31（一个季度）。" in prompt
    assert "距离判定日 2026-12-31 还有 0 天。" in prompt
    # 直接喂 step：结果权重生效
    ws = WorldState(["S1", "S2"], base_rates={"S1": 0.5, "S2": 0.5}, inertia=0.0)
    ws.step(out)
    assert ws.shares["S1"] == 1.0
    # degrade-safe：空 roster / 空 ctx → []
    assert elicit_round([], ctx) == []
    assert elicit_round(roster, {}) == []


def test_legacy_prompt_round_framing_unchanged():
    """hours 路径提示词逐字节不变：仍是"第 N 轮（对应时点约 …）"框架，无时段块。"""
    active = [{"agent_id": 1, "name": "A", "stance": "pro", "influence": 1.0}]
    p = _build_round_decision_prompt(["S1", "S2"], active, 3, "2027-01-01",
                                     base_shares={"S1": 0.6, "S2": 0.4})
    assert "第 3 轮（对应时点约 2027-01-01）的活跃角色" in p
    assert "时段：" not in p and "判定日" not in p
