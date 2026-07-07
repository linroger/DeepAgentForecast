"""ITEM 20 — 模拟真实感四件套的离线测试。

覆盖两层：
  A) 纯函数（app.services.agent_dynamics）：采样器确定性、节流数学、比例侦测器、小时记账、
     动作分桶——无 camel/oasis/DB。
  B) 运行时接入（run_parallel_simulation）：种子 FOLLOW 节流、本轮新帖取数、参与度采样注入
     （FakeEnv + 真 sqlite），以及 run_summary 的 simulated_hours / organic_ratio_warnings 落账。

四个修复均 env 门控、默认开、降级安全——测试同时钉住「开」的行为与「关/异常」的降级。
"""

import asyncio
import json
import os
import random
import sqlite3
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_BACKEND, "scripts")
for _p in (_BACKEND, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.agent_dynamics import (  # noqa: E402
    classify_organic_action,
    detect_organic_ratio_collapse,
    sample_engagement_likes,
    simulated_hours_from_rounds,
    throttle_seed_follows,
)


# ===========================================================================
# A) 纯函数
# ===========================================================================

# ----------------------------------------------------------- 采样器：确定性
def test_sampler_deterministic_same_seed_same_result():
    weights = {10: 1.0, 11: 3.0, 12: 2.0}
    authors = {10: 1, 11: 2, 12: 3}
    likers = [1, 2, 3, 4, 5]
    a = sample_engagement_likes(weights, authors, likers, 0.6, random.Random(42))
    b = sample_engagement_likes(weights, authors, likers, 0.6, random.Random(42))
    assert a == b                      # 同种子 → 逐字节相同
    assert a == sorted(a)              # 稳定升序
    # 每个 liker 至多一次
    assert len({liker for liker, _ in a}) == len(a)


def test_sampler_no_self_like():
    # 唯一候选帖的作者恰是唯一点赞者 → 无可赞帖，返回空
    out = sample_engagement_likes({7: 5.0}, {7: 3}, [3], 1.0, random.Random(1))
    assert out == []


def test_sampler_rate_zero_and_negative_return_empty():
    w, au, lk = {1: 1.0}, {1: 9}, [2, 3]
    assert sample_engagement_likes(w, au, lk, 0.0, random.Random(0)) == []
    assert sample_engagement_likes(w, au, lk, -1.0, random.Random(0)) == []


def test_sampler_rate_one_every_liker_participates():
    # rate=1 且每人都有可赞帖（作者不在点赞者内）→ 每个点赞者恰产生一次
    out = sample_engagement_likes({100: 1.0}, {100: 999}, [1, 2, 3], 1.0, random.Random(7))
    assert sorted(liker for liker, _ in out) == [1, 2, 3]
    assert all(pid == 100 for _, pid in out)


def test_sampler_zero_weight_posts_excluded():
    # 权重<=0 的帖不产生点赞；无正权重帖 → 空
    assert sample_engagement_likes({5: 0.0, 6: -2.0}, {5: 1, 6: 2}, [9], 1.0, random.Random(3)) == []


def test_sampler_no_likers_or_no_posts():
    assert sample_engagement_likes({}, {}, [1, 2], 1.0, random.Random(0)) == []
    assert sample_engagement_likes({1: 1.0}, {1: 5}, [], 1.0, random.Random(0)) == []


def test_sampler_weight_bias_favors_heavier_post():
    # 极端权重对比：几乎所有点赞落在高权重帖（统计性质，非严格但稳健）
    weights = {1: 1.0, 2: 100.0}
    authors = {1: 90, 2: 91}      # 作者不在点赞者集合内
    likers = list(range(200))
    out = sample_engagement_likes(weights, authors, likers, 1.0, random.Random(2025))
    on_heavy = sum(1 for _, pid in out if pid == 2)
    assert on_heavy > len(out) * 0.8


# ----------------------------------------------------------- 节流数学
def test_throttle_caps_per_follower_and_counts_dropped():
    by = {1: [10, 11, 12, 13, 14], 2: [20], 3: [30, 31, 32, 33]}
    capped, dropped = throttle_seed_follows(by, 3)
    assert capped[1] == [10, 11, 12]   # 5→3，丢 2
    assert capped[2] == [20]           # 1≤3，原样
    assert capped[3] == [30, 31, 32]   # 4→3，丢 1
    assert dropped == 3
    # 不改入参
    assert by[1] == [10, 11, 12, 13, 14]


def test_throttle_cap_zero_or_negative_is_unlimited():
    by = {1: [1, 2, 3, 4]}
    for cap in (0, -5, None):
        capped, dropped = throttle_seed_follows(by, cap)
        assert capped == {1: [1, 2, 3, 4]}
        assert dropped == 0


def test_throttle_returns_fresh_dict():
    by = {1: [1, 2, 3, 4]}
    capped, _ = throttle_seed_follows(by, 2)
    capped[1].append(999)
    assert by[1] == [1, 2, 3, 4]       # 入参未被污染


# ----------------------------------------------------------- 比例侦测器
def _rc(posts, comments, likes):
    return {"posts": posts, "comments": comments, "likes": likes}


def test_ratio_detector_flags_consecutive_collapse():
    prc = {
        "twitter": {
            1: _rc(5, 2, 3),           # 健康
            2: _rc(4, 0, 0),           # 塌缩
            3: _rc(6, 0, 0),           # 塌缩
            4: _rc(3, 0, 0),           # 塌缩 → 连续 3 轮
            5: _rc(2, 1, 0),           # 恢复
        }
    }
    w = detect_organic_ratio_collapse(prc, 3)
    assert len(w) == 1
    assert w[0]["platform"] == "twitter"
    assert w[0]["start_round"] == 2 and w[0]["end_round"] == 4
    assert w[0]["rounds"] == 3


def test_ratio_detector_below_threshold_no_warning():
    prc = {"reddit": {1: _rc(3, 0, 0), 2: _rc(4, 0, 0)}}  # 仅 2 连续 < K=3
    assert detect_organic_ratio_collapse(prc, 3) == []


def test_ratio_detector_dead_round_does_not_break_streak():
    # 设计约定（见 detect_organic_ratio_collapse 注释）：死轮（rounds_map 里没有该键、无任何
    # 动作）不是「恢复」，不打断塌缩连续段——只有真正出现 comments/likes 的轮才算恢复。
    # 这里轮 3 缺席，轮 1/2/4/5 均塌缩 → 视作一段长 4 的塌缩（跨死轮延续）。
    prc = {"twitter": {1: _rc(3, 0, 0), 2: _rc(3, 0, 0), 4: _rc(3, 0, 0), 5: _rc(3, 0, 0)}}
    w = detect_organic_ratio_collapse(prc, 3)
    assert len(w) == 1
    assert w[0]["rounds"] == 4
    assert w[0]["start_round"] == 1 and w[0]["end_round"] == 5


def test_ratio_detector_recovery_round_breaks_streak():
    # 真正的恢复轮（有 likes）确实打断连续段：段1=[1,2]（长2）、段2=[4,5]（长2），K=3 → 无告警
    prc = {"twitter": {1: _rc(3, 0, 0), 2: _rc(3, 0, 0), 3: _rc(3, 1, 1), 4: _rc(3, 0, 0), 5: _rc(3, 0, 0)}}
    assert detect_organic_ratio_collapse(prc, 3) == []


def test_ratio_detector_likes_present_not_collapsed():
    prc = {"twitter": {1: _rc(3, 0, 2), 2: _rc(3, 0, 1), 3: _rc(3, 0, 4)}}
    assert detect_organic_ratio_collapse(prc, 3) == []


def test_ratio_detector_min_consecutive_clamped_to_one():
    prc = {"twitter": {1: _rc(1, 0, 0)}}
    w = detect_organic_ratio_collapse(prc, 0)   # <1 视作 1
    assert len(w) == 1 and w[0]["rounds"] == 1


# ----------------------------------------------------------- 小时记账
def test_simulated_hours_formula():
    assert simulated_hours_from_rounds(24, 60) == 24.0
    assert simulated_hours_from_rounds(6, 30) == 3.0
    assert simulated_hours_from_rounds(10, 15) == 2.5


def test_simulated_hours_degrades_on_bad_input():
    assert simulated_hours_from_rounds(0, 60) == 0.0
    assert simulated_hours_from_rounds(-3, 60) == 0.0
    assert simulated_hours_from_rounds(5, 0) == 0.0
    assert simulated_hours_from_rounds("x", 60) == 0.0
    assert simulated_hours_from_rounds(5, None) == 0.0


# ----------------------------------------------------------- 动作分桶
def test_classify_organic_action_buckets():
    assert classify_organic_action("CREATE_POST") == "posts"
    assert classify_organic_action("quote_post") == "posts"      # 大小写无关
    assert classify_organic_action("REPOST") == "posts"
    assert classify_organic_action("CREATE_COMMENT") == "comments"
    assert classify_organic_action("LIKE_POST") == "likes"
    assert classify_organic_action("DISLIKE_POST") == "likes"    # 反应类归 likes
    assert classify_organic_action("LIKE_COMMENT") == "likes"
    assert classify_organic_action("FOLLOW") is None
    assert classify_organic_action("SEARCH_POSTS") is None
    assert classify_organic_action("") is None
    assert classify_organic_action(None) is None


# ===========================================================================
# B) 运行时接入（FakeEnv + 真 sqlite）
# ===========================================================================
import run_parallel_simulation as rps  # noqa: E402


class _FakeAgent:
    def __init__(self, agent_id):
        self.agent_id = agent_id


class _FakeGraph:
    def __init__(self, ids):
        self._agents = {i: _FakeAgent(i) for i in ids}

    def get_agent(self, i):
        return self._agents[i]

    def add_edge(self, a, b):
        pass


class _FakeEnv:
    """记录每次 env.step 收到的动作；可选把 LIKE_POST 写进 trace 表以模拟真实平台。"""

    def __init__(self, ids, db_path=None):
        self.agent_graph = _FakeGraph(ids)
        self.steps = []
        self.db_path = db_path

    async def step(self, actions):
        self.steps.append(actions)
        if self.db_path:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            for agent, ma in actions.items():
                atype = getattr(ma.action_type, "value", "like_post")
                info = json.dumps(ma.action_args)
                cur.execute("INSERT INTO trace VALUES (?, ?, ?, 't')",
                            (agent.agent_id, atype, info))
            conn.commit()
            conn.close()


def _make_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE trace (user_id INTEGER, action TEXT, info TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE follow (follow_id INTEGER, follower_id INTEGER, followee_id INTEGER)")
    cur.execute("CREATE TABLE user (user_id INTEGER, agent_id INTEGER, name TEXT, user_name TEXT)")
    cur.execute("CREATE TABLE post (post_id INTEGER, user_id INTEGER, content TEXT, "
                "original_post_id INTEGER, quote_content TEXT)")
    cur.execute("CREATE TABLE comment (comment_id INTEGER, user_id INTEGER, content TEXT)")
    conn.commit()
    return conn


class _RecLogger:
    def __init__(self):
        self.actions = []

    def log_action(self, **kw):
        self.actions.append(kw)


# ----------------------------------------------------------- 种子 FOLLOW 节流接入
def test_inject_initial_follows_throttled(monkeypatch):
    monkeypatch.setenv("SIM_MAX_FOLLOWS_PER_AGENT_ROUND", "2")
    env = _FakeEnv([1])
    # follower 1 有 5 个 followee → 节流到 2 → 只两趟 step
    cfg = {"initial_follows": [[1, 10], [1, 11], [1, 12], [1, 13], [1, 14]]}
    applied = asyncio.run(rps.inject_initial_follows(env, cfg, lambda m: None, {1: "A", 10: "B"}))
    assert applied == 2
    assert len(env.steps) == 2


def test_inject_initial_follows_unlimited_when_cap_zero(monkeypatch):
    monkeypatch.setenv("SIM_MAX_FOLLOWS_PER_AGENT_ROUND", "0")
    env = _FakeEnv([1])
    cfg = {"initial_follows": [[1, 10], [1, 11], [1, 12]]}
    applied = asyncio.run(rps.inject_initial_follows(env, cfg, lambda m: None, {1: "A"}))
    assert applied == 3            # 不限 → 全量注入


# ----------------------------------------------------------- 本轮新帖取数
def test_fetch_round_posts_and_watermark(tmp_path):
    db = str(tmp_path / "twitter.db")
    conn = _make_db(db)
    cur = conn.cursor()
    cur.execute("INSERT INTO user VALUES (100, 5, 'E', 'e')")
    cur.execute("INSERT INTO user VALUES (101, 6, 'F', 'f')")
    cur.execute("INSERT INTO post VALUES (1, 100, 'seed', NULL, NULL)")
    cur.execute("INSERT INTO post VALUES (2, 101, 'p2', NULL, NULL)")
    conn.commit()
    conn.close()
    assert rps._max_post_id(db) == 2
    posts, hw = rps._fetch_round_posts(db, 1, {})   # 只取 post_id>1
    assert posts == {2: 6}
    assert hw == 2
    # 缺库 → 降级
    assert rps._fetch_round_posts(str(tmp_path / "missing.db"), 0, {}) == ({}, 0)


# ----------------------------------------------------------- 参与度采样注入
def test_inject_engagement_likes_injects_and_logs(tmp_path, monkeypatch):
    db = str(tmp_path / "twitter.db")
    conn = _make_db(db)
    cur = conn.cursor()
    # 两位作者的两条新帖；点赞者是第三方 agent（避免自赞）
    cur.execute("INSERT INTO user VALUES (100, 1, 'A', 'a')")
    cur.execute("INSERT INTO user VALUES (101, 2, 'B', 'b')")
    cur.execute("INSERT INTO post VALUES (50, 100, 'p1', NULL, NULL)")
    cur.execute("INSERT INTO post VALUES (51, 101, 'p2', NULL, NULL)")
    conn.commit()
    conn.close()

    env = _FakeEnv([1, 2, 3, 4], db_path=db)
    active = [(3, env.agent_graph.get_agent(3)), (4, env.agent_graph.get_agent(4))]
    state = {"last_post_id": 49}        # 水位在两帖之前 → 两帖都算本轮新帖
    logger = _RecLogger()
    names = {1: "A", 2: "B", 3: "C", 4: "D"}
    injected, new_rowid = asyncio.run(rps.inject_engagement_likes(
        env, db, state, active, [], round_num=2, agent_names=names,
        action_logger=logger, rng=random.Random(11), rate=1.0, last_rowid=0,
        log_info=lambda m: None,
    ))
    assert injected == 2                              # rate=1，两个活跃者各一赞
    assert env.steps and len(env.steps[0]) == 2       # 注入了一次 env.step，两条 LIKE
    assert state["last_post_id"] == 51                # 水位推进
    # 记录都带 is_engagement_sample 标记，round 用 round_num+1
    for rec in logger.actions:
        assert rec["action_type"] == "LIKE_POST"
        assert rec["action_args"]["is_engagement_sample"] is True
        assert rec["round_num"] == 3
        assert rec["action_args"]["post_id"] in (50, 51)


def test_inject_engagement_likes_no_new_posts_is_noop(tmp_path):
    db = str(tmp_path / "twitter.db")
    conn = _make_db(db)
    conn.close()
    env = _FakeEnv([1], db_path=db)
    active = [(1, env.agent_graph.get_agent(1))]
    injected, rowid = asyncio.run(rps.inject_engagement_likes(
        env, db, {"last_post_id": 0}, active, [], 1, {1: "A"},
        _RecLogger(), random.Random(0), 1.0, 7, lambda m: None,
    ))
    assert injected == 0 and rowid == 7 and env.steps == []
