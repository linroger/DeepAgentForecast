"""Calendar event bucketing — TEMPORAL spec §7 item 3.

日历模式的 key_events 落轮契约：
* 区间包含定位（含首回合起点日 / 回合边界日 / 判定日当天）；
* 早于/等于 as_of 的事件丢弃（既有政策）；
* 晚于判定日的事件完整留档到 temporal_config.beyond_horizon_events；
* 日期不可解析的条目排除并计入 ``event_date_unparsed:<n>`` warning；
* 小时制 events_to_schedule / _build_scheduled_events 旧路径逐字节不变（回归钉）。

Offline：零 LLM / 零网络。Generator 实例按既有测试约定用 __new__ 构建。
"""

from dataclasses import asdict
from datetime import date

from app.config import Config
from app.services.simulation_config_generator import (
    AgentActivityConfig,
    SimulationConfigGenerator,
    TimeSimulationConfig,
)
from app.utils.actors import events_to_calendar_rounds, events_to_schedule
from app.utils.sim_timeline import HorizonResult, build_round_periods, build_timeline


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

AS_OF = date(2026, 7, 11)
HORIZON = date(2026, 12, 31)
# month 网格：r0=2026-07-12..07-31, r1=08, r2=09, r3=10, r4=11, r5=12-01..12-31
ROUNDS = build_round_periods(AS_OF, HORIZON, "month")


def _gen():
    return SimulationConfigGenerator.__new__(SimulationConfigGenerator)


def _agents(specs):
    """[(agent_id, name, influence), ...] → AgentActivityConfig list."""
    return [
        AgentActivityConfig(agent_id=i, entity_uuid=f"u{i}", entity_name=n,
                            entity_type="Organization", influence_weight=w)
        for (i, n, w) in specs
    ]


def _timeline():
    """as_of 2026-07-11 → horizon 2026-12-31 的日历时间线（确定性，无 LLM）。"""
    return build_timeline(
        AS_OF,
        HorizonResult("2026-12-31", "explicit_date", "2026-12-31", False, 1.0),
        target_max=36,
    )


# ---------------------------------------------------------------------------
# events_to_calendar_rounds：区间包含 + 边界日
# ---------------------------------------------------------------------------

def test_containment_placement_including_boundary_dates():
    assert len(ROUNDS) == 6  # 首残段(07-12..07-31) + 08..11 整月 + 12 月
    actors = {"key_events": [
        {"date": "2026-07-12", "event": "first period start day"},
        {"date": "2026-07-31", "event": "first period end day"},
        {"date": "2026-08-01", "event": "second period start day"},
        {"date": "2026-10-15", "event": "mid-october"},
        {"date": "2026-12-31", "event": "horizon day itself"},
    ]}
    sched, beyond = events_to_calendar_rounds(actors, ROUNDS, "2026-07-11", "2026-12-31")
    assert beyond == []
    assert {s["date"]: s["round"] for s in sched} == {
        "2026-07-12": 0,
        "2026-07-31": 0,
        "2026-08-01": 1,
        "2026-10-15": 3,
        "2026-12-31": 5,
    }
    # 输出条目形状与 events_to_schedule 一致（poster 字段由调用方补齐）
    assert all(set(s) == {"round", "event", "date"} for s in sched)


def test_round_dates_accept_json_dict_rows():
    """config 回读场景：round_dates 是 JSON dict 行而非 RoundPeriod。"""
    rows = [asdict(rp) for rp in ROUNDS]
    sched, _ = events_to_calendar_rounds(
        {"key_events": [{"date": "2026-09-10", "event": "x"}]},
        rows, "2026-07-11", "2026-12-31",
    )
    assert [s["round"] for s in sched] == [2]


# ---------------------------------------------------------------------------
# 过去事件丢弃；判定日外事件完整留档
# ---------------------------------------------------------------------------

def test_past_events_dropped():
    actors = {"key_events": [
        {"date": "2026-07-11", "event": "on as_of day"},   # d == as_of → 丢弃
        {"date": "2026-01-01", "event": "long past"},
        {"date": "2026-09-10", "event": "in window"},
    ]}
    sched, beyond = events_to_calendar_rounds(actors, ROUNDS, "2026-07-11", "2026-12-31")
    assert beyond == []
    assert [s["date"] for s in sched] == ["2026-09-10"]


def test_beyond_horizon_returned_as_full_items():
    ev = {"date": "2027-03-01", "event": "post-horizon", "extra": "keep-me"}
    actors = {"key_events": [ev, {"date": "2026-09-10", "event": "in window"}]}
    sched, beyond = events_to_calendar_rounds(actors, ROUNDS, "2026-07-11", "2026-12-31")
    assert [s["date"] for s in sched] == ["2026-09-10"]
    assert beyond == [ev]           # 完整条目（含非标准键）
    assert beyond[0] is not ev      # 浅拷贝，不共享原 dict


def test_degrade_on_bad_inputs():
    assert events_to_calendar_rounds(None, ROUNDS, "2026-07-11", "2026-12-31") == ([], [])
    assert events_to_calendar_rounds({"key_events": []}, [], "2026-07-11", "2026-12-31") == ([], [])
    assert events_to_calendar_rounds({"key_events": "junk"}, ROUNDS, "2026-07-11", "2026-12-31") == ([], [])
    assert events_to_calendar_rounds({"key_events": []}, ROUNDS, "someday", "2026-12-31") == ([], [])


# ---------------------------------------------------------------------------
# _build_scheduled_events 日历分支：warnings / beyond 留档 / "[日期] " 前缀
# ---------------------------------------------------------------------------

def test_unparseable_counted_in_warnings_and_beyond_persisted():
    g = _gen()
    timeline = _timeline()
    tc = TimeSimulationConfig()
    agents = _agents([(0, "PRC", 3.0), (1, "US Federal Executive", 2.5)])
    actors = {
        "as_of_date": "2026-07-11",
        "key_events": [
            {"date": "2026-09-10", "event": "PRC announces export rule"},
            {"date": "someday", "event": "no date"},
            {"date": "TBD", "event": "no date either"},
            {"date": "2027-05-01", "event": "beyond horizon"},
            {"date": "2026-06-01", "event": "past"},
        ],
    }
    events = g._build_scheduled_events(actors, tc, agents, timeline=timeline)
    # warning：2 个不可解析日期
    assert "event_date_unparsed:2" in timeline.warnings
    # 判定日外事件完整留档
    assert timeline.beyond_horizon_events == [{"date": "2027-05-01", "event": "beyond horizon"}]
    # 落轮事件：内容带 "[日期] " 前缀，poster 字段齐全，round 在时间线范围内
    assert len(events) == 1
    ev = events[0]
    assert ev["content"] == "[2026-09-10] PRC announces export rule"
    assert set(ev) == {"round", "content", "date", "poster_agent_id", "poster_name"}
    assert 0 <= ev["round"] < timeline.n_rounds
    assert ev["poster_name"] == "PRC"  # 事件文本命中真实角色


# ---------------------------------------------------------------------------
# 小时制旧路径逐字节不变（回归钉）
# ---------------------------------------------------------------------------

_LEGACY_ACTORS = {
    "as_of_date": "2026-07-01",
    "key_events": [
        {"date": "2026-08-01", "event": "A"},
        {"date": "2026-12-01", "event": "B"},
        {"date": "junk", "event": "C"},
        {"date": "2026-06-01", "event": "past"},
    ],
}


def test_events_to_schedule_legacy_path_byte_identical(monkeypatch):
    monkeypatch.setattr(Config, "SIM_EVENT_REACT_BUFFER", True, raising=False)
    out = events_to_schedule(_LEGACY_ACTORS, total_rounds=72, as_of_date="2026-07-01")
    # hz=153（最远未来事件），react buffer → effective=58：
    # A: round(31/153*58)=12；B: min(71, round(153/153*58))=58
    assert out == [
        {"round": 12, "event": "A", "date": "2026-08-01"},
        {"round": 58, "event": "B", "date": "2026-12-01"},
    ]


def test_build_scheduled_events_hours_path_unchanged(monkeypatch):
    """timeline 未传（小时制）→ 走 events_to_schedule 旧映射，内容无日期前缀。"""
    monkeypatch.setattr(Config, "SIM_EVENT_REACT_BUFFER", True, raising=False)
    monkeypatch.setattr(Config, "SIM_SCHEDULE_CLAMP_ROUNDS", False, raising=False)
    g = _gen()
    tc = TimeSimulationConfig(total_simulation_hours=72, minutes_per_round=60)
    agents = _agents([(0, "PRC", 3.0)])
    events = g._build_scheduled_events(_LEGACY_ACTORS, tc, agents)
    assert [e["content"] for e in events] == ["A", "B"]  # 无 "[日期] " 前缀
    assert [e["round"] for e in events] == [12, 58]


# ---------------------------------------------------------------------------
# cadence：默认 sampled；principal 分层（非受众、≥0.6、前 20、并列按 id 升序）
# ---------------------------------------------------------------------------

def test_agent_config_cadence_defaults_to_sampled_and_serializes():
    cfg = AgentActivityConfig(agent_id=0, entity_uuid="u", entity_name="X",
                              entity_type="Organization")
    assert cfg.cadence == "sampled"
    assert asdict(cfg)["cadence"] == "sampled"


def test_assign_cadence_tiers_top20_tiebreak_and_audience_excluded():
    g = _gen()
    agents = _agents(
        [(i, f"A{i}", 3.0) for i in range(19)]        # 19 个并列 3.0
        + [(19, "B19", 0.7), (20, "B20", 0.7), (21, "B21", 0.7), (22, "B22", 0.7)]
        + [(23, "C23", 0.5)]                          # 低于 0.6 门槛
    )
    audience = AgentActivityConfig(agent_id=24, entity_uuid="audience:24",
                                   entity_name="公众_0", entity_type="Audience",
                                   influence_weight=3.0)
    agents.append(audience)
    n = g._assign_cadence_tiers(agents)
    assert n == 20
    by_id = {c.agent_id: c.cadence for c in agents}
    # 3.0 并列 19 个全部入选 + 0.7 档并列按 agent_id 升序取 id=19
    assert all(by_id[i] == "principal" for i in range(20))
    assert by_id[20] == by_id[21] == by_id[22] == "sampled"
    assert by_id[23] == "sampled"   # 影响力 < 0.6
    assert by_id[24] == "sampled"   # 受众永远 sampled
