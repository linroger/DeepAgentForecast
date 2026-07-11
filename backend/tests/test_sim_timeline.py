"""sim_timeline 纯函数测试（temporal_sim_spec §2 / §7-1）。

零 LLM、零 I/O：horizon 四层抽取表（EN/CN ≥30 例 + 拒绝 + 永不抛异常）、
§2.3 单位选择表逐行钉死（含 48d→day/48 与 49d→week/7 的锯齿、20 年 stride）、
划分/网格对齐/残段合并/闰年/round_for_date 边界，以及三条性质断言。
"""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.utils.sim_timeline import (
    HorizonResult,
    RoundPeriod,
    build_round_periods,
    build_timeline,
    default_horizon,
    extract_horizon,
    round_for_date,
    select_calendar_unit,
)

AS_OF = date(2026, 7, 11)  # spec §2.3 工作示例基准日（周六）


def _hr(iso, source="explicit_date"):
    return HorizonResult(iso, source, "x", False, 1.0)


def _timeline(text, target_max=36, **kw):
    hr = extract_horizon(text, AS_OF)
    assert hr is not None, f"extract_horizon 未命中: {text!r}"
    return build_timeline(AS_OF, hr, target_max, **kw)


def _assert_partition(periods, as_of, horizon):
    """无缝、无重叠、首回合起于 as_of+1、末回合恰止于 horizon。"""
    assert periods, "至少一个回合"
    assert periods[0].period_start == (as_of + timedelta(days=1)).isoformat()
    assert periods[-1].period_end == horizon.isoformat()
    prev_end = None
    for i, p in enumerate(periods):
        assert p.round == i  # 0-based 且连续
        s = date.fromisoformat(p.period_start)
        e = date.fromisoformat(p.period_end)
        assert s <= e
        if prev_end is not None:
            assert s == prev_end + timedelta(days=1), "回合间出现缝隙/重叠"
        prev_end = e


# ---------------------------------------------------------------------------
# extract_horizon：四层抽取表（EN/CN ≥ 30 例）
# ---------------------------------------------------------------------------

HORIZON_TABLE = [
    # --- tier 1: explicit_date ---
    ("The launch is scheduled for 2027-03-15.", "2027-03-15", "explicit_date"),
    ("deadline 2028/6/30 confirmed", "2028-06-30", "explicit_date"),
    ("target 2027.12.01 release window", "2027-12-01", "explicit_date"),
    ("先看 2020-01-01 的旧数据，再看 2027-05-01 的截止日", "2027-05-01", "explicit_date"),  # 过期候选跳过继续扫
    ("将于2027年3月15日前完成部署", "2027-03-15", "explicit_date"),
    ("2030年1月1日正式生效", "2030-01-01", "explicit_date"),
    ("2026年12月31日前必须落地", "2026-12-31", "explicit_date"),
    # --- tier 2: anchored_period ---
    ("shipping by end of 2027", "2027-12-31", "anchored_period"),
    ("expected late 2028", "2028-12-31", "anchored_period"),
    ("目标是2029年底完成", "2029-12-31", "anchored_period"),
    ("ship by mid-2027", "2027-06-30", "anchored_period"),
    ("around mid 2028", "2028-06-30", "anchored_period"),
    ("预计2030年中落地", "2030-06-30", "anchored_period"),
    ("results due in Q3 2027", "2027-09-30", "anchored_period"),
    ("2028 Q1 earnings call", "2028-03-31", "anchored_period"),
    ("2027年Q2 见分晓", "2027-06-30", "anchored_period"),
    ("2028年第三季度完成合并", "2028-09-30", "anchored_period"),
    ("H2 2027 rollout", "2027-12-31", "anchored_period"),
    ("2027年H1 完成融资", "2027-06-30", "anchored_period"),
    ("2028年上半年出结果", "2028-06-30", "anchored_period"),
    ("2029年下半年之前", "2029-12-31", "anchored_period"),
    ("no later than December 2028", "2028-12-31", "anchored_period"),
    ("by March 2027 at the latest", "2027-03-31", "anchored_period"),
    ("due Feb 2028", "2028-02-29", "anchored_period"),  # 闰年月末
    ("2027年11月前签约", "2027-11-30", "anchored_period"),
    # --- tier 3: relative（真日历算术，绝不 N×30） ---
    ("will it settle in 6 months?", "2027-01-11", "relative"),
    ("within 3 weeks", "2026-08-01", "relative"),
    ("next 10 days are critical", "2026-07-21", "relative"),
    ("over the next 2 quarters", "2027-01-11", "relative"),
    ("in 5 years we will know", "2031-07-11", "relative"),
    ("未来三年内实现盈利", "2029-07-11", "relative"),
    ("今后6个月的走势", "2027-01-11", "relative"),
    ("接下来两周见分晓", "2026-07-25", "relative"),
    ("未来十天内公布", "2026-07-21", "relative"),
    ("未来18个月能否翻倍", "2028-01-11", "relative"),
    ("接下来一年的竞争格局", "2027-07-11", "relative"),
    # --- tier 4: bare_year（取范围内最大年份 → 12-31） ---
    ("who wins the US AI race by 2030?", "2030-12-31", "bare_year"),
    ("2027 vs 2029, which one matters?", "2029-12-31", "bare_year"),
    ("回顾2026、展望未来", "2026-12-31", "bare_year"),
    ("as of 2026-07-11 who leads?", "2026-12-31", "bare_year"),  # tier1 候选==as_of 被跳过 → 回落 bare_year
]


@pytest.mark.parametrize("text,expected_iso,expected_source", HORIZON_TABLE,
                         ids=[t[0][:40] for t in HORIZON_TABLE])
def test_extract_horizon_table(text, expected_iso, expected_source):
    hr = extract_horizon(text, AS_OF)
    assert hr is not None
    assert hr.horizon_date == expected_iso
    assert hr.source == expected_source
    assert hr.confidence == 1.0
    assert hr.defaulted is False
    assert hr.matched_text  # 确定性层必须回填命中片段


def test_extract_horizon_table_covers_at_least_30_cases():
    assert len(HORIZON_TABLE) >= 30


@pytest.mark.parametrize("text", [
    "value 20301 units",            # 五位数不是年份（(?<!\\d)…(?!\\d)）
    "settled back in 2020",         # 过去年份
    "过去的2019年已经证明过",         # 过去年份（CN）
    "maybe by 2099 it happens",     # > as_of + 30y
    "no dates here at all",
    "9102年不存在",
    "",
])
def test_extract_horizon_rejects(text):
    assert extract_horizon(text, AS_OF) is None


def test_extract_horizon_never_raises_on_garbage():
    garbage = [
        None, 12345, ["list"], {"k": "v"}, b"bytes",
        "", "   ", "\x00\x9f\t", "🤖🚀" * 500, "２０２７年（全角）",
        "in ∞ months", "null", "20-30-40", "2027" * 3000,
        "....2027-99-99....", "2026年13月40日", "in 99999 years",
        "Q9 2027", "H3 2028", "未来〇年",
    ]
    for g in garbage:
        result = extract_horizon(g, AS_OF)  # 契约：永不抛异常
        assert result is None or isinstance(result, HorizonResult)


def test_extract_horizon_tier_precedence():
    # explicit_date 层胜过后面所有层
    hr = extract_horizon("A: 2027-06-30 deadline. B: late 2029.", AS_OF)
    assert hr.horizon_date == "2027-06-30" and hr.source == "explicit_date"


def test_extract_horizon_prompt_outranks_question_within_tier():
    # text = prompt + "\n" + central_question：层内先出现者（prompt）优先
    hr = extract_horizon("prompt: by end of 2027\nquestion: by end of 2029", AS_OF)
    assert hr.horizon_date == "2027-12-31"


def test_extract_horizon_bare_year_takes_max():
    hr = extract_horizon("从2027到2033的十年之外还提了2031", AS_OF)
    # tier3 先命中「十年」？——关键词（未来/今后/接下来/in…）都不在，「的十年」无关键词前缀 → 不命中
    assert hr.source == "bare_year" and hr.horizon_date == "2033-12-31"


def test_extract_horizon_relative_clamps_month_end():
    # 月末溢出钳制：2026-07-31 + 7 个月 → 2027-02-28（非闰年）
    hr = extract_horizon("in 7 months", date(2026, 7, 31))
    assert hr.horizon_date == "2027-02-28"


def test_extract_horizon_accepts_datetime_as_of():
    hr = extract_horizon("by 2030", datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc))
    assert hr is not None and hr.horizon_date == "2030-12-31"


def test_default_horizon():
    hr = default_horizon(AS_OF)
    assert hr == HorizonResult("2027-07-11", "default", "", True, 0.3)
    assert default_horizon(AS_OF, months=24).horizon_date == "2028-07-11"
    # 闰日基准 + 真日历加法钳制
    assert default_horizon(date(2028, 2, 29), 12).horizon_date == "2029-02-28"


# ---------------------------------------------------------------------------
# select_calendar_unit：§2.3 工作示例表逐行钉死
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("span,unit,stride", [
    (21, "day", 1),        # 3 周
    (36, "day", 1),
    (37, "day", 1),        # 防锯齿细化：week 只有 6 轮 → 细化回 day（37 ≤ hard_max）
    (48, "day", 1),        # 锯齿此侧：48 ≤ hard_max
    (49, "week", 1),       # 锯齿彼侧：day 需 49 > 48 → 停在 week
    (548, "month", 1),     # 18 个月
    (1269, "quarter", 1),  # by 2029
    (1634, "quarter", 1),  # by 2030
    (3460, "half_year", 1),  # by 2035
    (3461, "half_year", 1),
    (7305, "half_year", 2),  # 20 年：stride 保全覆盖，绝不截断
])
def test_select_calendar_unit_pinned_table(span, unit, stride):
    assert select_calendar_unit(span, target_max=36, hard_max=48) == (unit, stride)


def test_select_calendar_unit_degenerate_inputs():
    # span/target_max 非法值钳到 1，不抛异常
    assert select_calendar_unit(0, 36) == ("day", 1)
    assert select_calendar_unit(-5, 36) == ("day", 1)
    unit, stride = select_calendar_unit(7305, 1)
    assert unit == "half_year" and stride >= 1


def test_sawtooth_48_49_end_to_end():
    """§2.3 钉死的唯一残留锯齿：48d→day/48 vs 49d→week/7。"""
    t48 = build_timeline(AS_OF, _hr((AS_OF + timedelta(days=48)).isoformat()), 36)
    assert (t48.unit, t48.n_rounds) == ("day", 48)
    t49 = build_timeline(AS_OF, _hr((AS_OF + timedelta(days=49)).isoformat()), 36)
    assert (t49.unit, t49.n_rounds) == ("week", 7)
    # 首桩合并进第二周：周日单日残段 (2026-07-12) 并入 [07-13..07-19]
    assert (t49.round_dates[0].period_start, t49.round_dates[0].period_end) == ("2026-07-12", "2026-07-19")
    assert all(re.fullmatch(r"\d{4}-W\d{2}", p.label) for p in t49.round_dates)


# ---------------------------------------------------------------------------
# build_timeline：工作示例端到端（unit + n_rounds = len(round_dates)）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,unit,stride,n_rounds", [
    ("in 3 weeks", "day", 1, 21),
    ("by 2029", "quarter", 1, 14),
    ("who wins the US AI race by 2030?", "quarter", 1, 18),
    ("by 2035", "half_year", 1, 19),
    ("in 20 years", "half_year", 2, 21),   # 表中 ~21
    # spec §2.3 表写 month/18，但按其钉死算法 ceil(549/30.44)=19 且实际边界
    # （2026-07-12..2028-01-11，首 20d/尾 11d 均高于合并阈值）确为 19 轮。
    ("in 18 months", "month", 1, 19),
])
def test_build_timeline_worked_examples(text, unit, stride, n_rounds):
    tl = _timeline(text)
    assert tl.unit == unit
    assert tl.unit_stride == stride
    assert tl.n_rounds == n_rounds == len(tl.round_dates)
    _assert_partition(tl.round_dates, AS_OF, date.fromisoformat(tl.horizon_date))


def test_build_timeline_by_2030_pinned_rows():
    """§3 示例整行钉死：round 0 = 2026-Q3，末回合恰止于 2030-12-31。"""
    tl = _timeline("who wins the US AI race by 2030?")
    assert tl.schema_version == 1 and tl.mode == "calendar"
    assert tl.as_of_date == "2026-07-11"
    assert tl.horizon_date == "2030-12-31"
    assert tl.horizon_source == "bare_year" and tl.horizon_text == "2030"
    assert tl.horizon_defaulted is False
    assert tl.span_days == 1634
    assert tl.target_max_rounds == 36
    assert tl.round_cap_coarsened is None
    assert tl.beyond_horizon_events == [] and tl.warnings == []
    assert tl.round_dates[0] == RoundPeriod(0, "2026-07-12", "2026-09-30", "2026-Q3")
    assert tl.round_dates[-1] == RoundPeriod(17, "2030-10-01", "2030-12-31", "2030-Q4")
    # 网格对齐：内部回合一律始于季度首日（1/4/7/10 月 1 日）
    for p in tl.round_dates[1:]:
        s = date.fromisoformat(p.period_start)
        assert s.day == 1 and s.month in (1, 4, 7, 10)


def test_build_timeline_stride_grid_and_labels():
    tl = _timeline("in 20 years")
    assert "horizon_capped_stride" in tl.warnings
    _assert_partition(tl.round_dates, AS_OF, date.fromisoformat(tl.horizon_date))
    # stride=2 的完整内部回合恰为一年（两个 half_year），起点仍在 1 月 1 日网格上
    for p in tl.round_dates[1:-1]:
        s = date.fromisoformat(p.period_start)
        e = date.fromisoformat(p.period_end)
        assert (s.month, s.day) == (1, 1)
        assert e == date(s.year, 12, 31)
        assert p.label == f"{s.year}-H1~{s.year}-H2"


def test_build_timeline_determinism():
    a = _timeline("who wins the US AI race by 2030?")
    b = _timeline("who wins the US AI race by 2030?")
    assert a == b


# ---------------------------------------------------------------------------
# build_round_periods：划分、网格对齐、残段合并、闰年
# ---------------------------------------------------------------------------


def test_grid_snap_when_as_of_is_on_boundary():
    # as_of = 季度末 → 首回合即完整季度，无残段
    periods = build_round_periods(date(2026, 9, 30), date(2027, 9, 30), "quarter")
    assert [p.period_start for p in periods] == ["2026-10-01", "2027-01-01", "2027-04-01", "2027-07-01"]
    assert [p.label for p in periods] == ["2026-Q4", "2027-Q1", "2027-Q2", "2027-Q3"]
    _assert_partition(periods, date(2026, 9, 30), date(2027, 9, 30))


def test_first_stub_merges_into_second():
    # 首段 2026-09-26..09-30 仅 5 天 < 91.31/3 → 并入 Q4
    periods = build_round_periods(date(2026, 9, 25), date(2027, 9, 30), "quarter")
    assert len(periods) == 4
    assert (periods[0].period_start, periods[0].period_end) == ("2026-09-26", "2026-12-31")
    _assert_partition(periods, date(2026, 9, 25), date(2027, 9, 30))


def test_last_runt_merges_into_predecessor():
    # 末段 2027-10-01..10-05 仅 5 天 < 91.31/4 → 前一回合延伸到 horizon
    periods = build_round_periods(date(2026, 9, 25), date(2027, 10, 5), "quarter")
    assert len(periods) == 4
    assert (periods[-1].period_start, periods[-1].period_end) == ("2027-07-01", "2027-10-05")
    _assert_partition(periods, date(2026, 9, 25), date(2027, 10, 5))


def test_merges_skipped_when_they_would_leave_zero_rounds():
    # 两段皆为残段：首段合并后仅剩 1 回合，末段合并被跳过
    periods = build_round_periods(date(2026, 9, 28), date(2026, 10, 3), "quarter")
    assert len(periods) == 1
    assert (periods[0].period_start, periods[0].period_end) == ("2026-09-29", "2026-10-03")
    # 单日跨度：单回合，不崩
    single = build_round_periods(AS_OF, AS_OF + timedelta(days=1), "quarter")
    assert len(single) == 1 and single[0].period_start == single[0].period_end == "2026-07-12"
    # 空/负跨度 → []
    assert build_round_periods(AS_OF, AS_OF, "month") == []
    assert build_round_periods(AS_OF, AS_OF - timedelta(days=3), "month") == []


def test_half_month_grid_and_labels():
    periods = build_round_periods(AS_OF, date(2026, 9, 10), "half_month")
    # 首段 07-12..07-15 仅 4 天 < 15.22/3 → 并入 07-16..07-31
    assert [(p.period_start, p.period_end) for p in periods] == [
        ("2026-07-12", "2026-07-31"),
        ("2026-08-01", "2026-08-15"),
        ("2026-08-16", "2026-08-31"),
        ("2026-09-01", "2026-09-10"),
    ]
    assert [p.label for p in periods] == ["2026-07-H1", "2026-08-H1", "2026-08-H2", "2026-09-H1"]


def test_leap_year_february():
    # 闰年：2028-02 完整回合止于 02-29
    leap = build_round_periods(date(2028, 1, 31), date(2028, 6, 30), "month")
    assert (leap[0].period_start, leap[0].period_end) == ("2028-02-01", "2028-02-29")
    _assert_partition(leap, date(2028, 1, 31), date(2028, 6, 30))
    # 平年对照：止于 02-28
    plain = build_round_periods(date(2027, 1, 31), date(2027, 6, 30), "month")
    assert plain[0].period_end == "2027-02-28"
    assert [p.label for p in plain][:2] == ["2027-02", "2027-03"]


def test_day_unit_periods_are_single_days():
    periods = build_round_periods(AS_OF, AS_OF + timedelta(days=5), "day")
    assert len(periods) == 5
    for p in periods:
        assert p.period_start == p.period_end == p.label


def test_partition_property_sweep():
    """种子参数网格（非 hypothesis）：任意跨度×预算下划分不变量恒成立。"""
    for span in range(1, 4001, 137):
        horizon = AS_OF + timedelta(days=span)
        for target_max in (8, 16, 36, 48):
            unit, stride = select_calendar_unit(span, target_max)
            periods = build_round_periods(AS_OF, horizon, unit, stride)
            _assert_partition(periods, AS_OF, horizon)
            # 内部回合起点必须落在自然网格上
            for p in periods[1:]:
                s = date.fromisoformat(p.period_start)
                if unit == "week":
                    assert s.weekday() == 0
                elif unit == "half_month":
                    assert s.day in (1, 16)
                elif unit == "month":
                    assert s.day == 1
                elif unit == "quarter":
                    assert s.day == 1 and s.month in (1, 4, 7, 10)
                elif unit == "half_year":
                    assert s.day == 1 and s.month in (1, 7)


# ---------------------------------------------------------------------------
# round_for_date：边界语义
# ---------------------------------------------------------------------------


def test_round_for_date_edges():
    tl = _timeline("who wins the US AI race by 2030?")
    rd = tl.round_dates
    assert round_for_date(date(2020, 1, 1), rd) is None      # 远古
    assert round_for_date(AS_OF, rd) is None                 # as_of 当日属于过去
    assert round_for_date(date(2026, 7, 12), rd) == 0        # 首回合首日
    assert round_for_date(date(2026, 9, 30), rd) == 0        # 首回合末日
    assert round_for_date(date(2026, 10, 1), rd) == 1        # 边界翌日进下一回合
    assert round_for_date(date(2028, 2, 29), rd) == 6        # 闰日也可定位（2028-Q1）
    assert round_for_date(date(2030, 12, 31), rd) == 17      # horizon 当日 = 末回合
    assert round_for_date(date(2031, 1, 1), rd) is None      # 超出 horizon
    assert round_for_date([], []) is None                    # 空输入


def test_round_for_date_accepts_dict_rows_and_datetime():
    tl = _timeline("who wins the US AI race by 2030?")
    rows = [
        {"round": p.round, "period_start": p.period_start, "period_end": p.period_end, "label": p.label}
        for p in tl.round_dates
    ]
    assert round_for_date(date(2027, 5, 1), rows) == round_for_date(date(2027, 5, 1), tl.round_dates) == 3
    assert round_for_date(datetime(2027, 5, 1, 8, 0, tzinfo=timezone.utc), rows) == 3


# ---------------------------------------------------------------------------
# build_timeline：粗化检测与降级
# ---------------------------------------------------------------------------


def test_round_cap_coarsens_unit_never_truncates():
    hr = extract_horizon("by 2030", AS_OF)
    tl = build_timeline(AS_OF, hr, target_max=12)  # 显式回合上限 → 粗化到 half_year
    assert tl.unit == "half_year" and tl.unit_stride == 1
    assert tl.n_rounds == 9
    assert tl.round_cap_coarsened == {"from_unit": "quarter", "to_unit": "half_year", "cap": 12}
    assert "round_cap_coarsened" in tl.warnings
    assert tl.round_dates[-1].period_end == "2030-12-31"  # 覆盖到 horizon，绝不截断


def test_round_cap_extreme_falls_to_stride():
    hr = extract_horizon("by 2030", AS_OF)
    tl = build_timeline(AS_OF, hr, target_max=8)
    assert tl.unit == "half_year" and tl.unit_stride == 2
    assert tl.n_rounds == 5
    assert "round_cap_coarsened" in tl.warnings and "horizon_capped_stride" in tl.warnings
    assert tl.round_dates[-1].period_end == "2030-12-31"


def test_round_cap_same_unit_no_coarsen_flag():
    hr = extract_horizon("by 2030", AS_OF)
    tl = build_timeline(AS_OF, hr, target_max=20)  # quarter 在 20 轮内仍可容纳
    assert tl.unit == "quarter" and tl.round_cap_coarsened is None
    assert "round_cap_coarsened" not in tl.warnings


def test_invalid_horizon_falls_back_to_default_12_months():
    tl = build_timeline(AS_OF, _hr("2020-01-01"), 36)  # horizon ≤ as_of
    assert tl.horizon_date == "2027-07-11"
    assert tl.horizon_source == "default" and tl.horizon_defaulted is True
    assert "horizon_invalid_defaulted" in tl.warnings
    assert tl.span_days == 365 and tl.n_rounds == len(tl.round_dates) > 0


def test_defaulted_horizon_warning_and_shape():
    tl = build_timeline(AS_OF, default_horizon(AS_OF), 36)
    assert "horizon_defaulted_12_months" in tl.warnings
    assert tl.horizon_defaulted is True and tl.horizon_text == ""
    assert tl.unit == "month" and tl.n_rounds == 13
    _assert_partition(tl.round_dates, AS_OF, date(2027, 7, 11))


# ---------------------------------------------------------------------------
# 三条性质断言（spec §2.3 Properties / §7-1）
# ---------------------------------------------------------------------------


def test_property_three_week_question_never_gets_quarters():
    for target_max in range(8, 49):
        unit, _ = select_calendar_unit(21, target_max)
        assert unit != "quarter"
        assert unit == "day"  # 实际恒为 day（防锯齿细化）


def test_property_ten_year_question_never_gets_days():
    for target_max in range(8, 49):
        unit, _ = select_calendar_unit(3653, target_max)
        assert unit != "day"


def test_property_rounds_grow_with_horizon():
    r2029 = _timeline("by 2029").n_rounds
    r2035 = _timeline("by 2035").n_rounds
    assert r2035 > r2029  # 19 > 14
    assert (r2029, r2035) == (14, 19)
