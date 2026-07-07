"""Per-agent affective-state dynamics for multi-round OASIS simulation (EXECPLAN2 I-2-1).

Without intra-agent state evolution a multi-round simulation is just N independent
one-shot polls repeated T times — no escalation, no bandwagon, no outrage fatigue.
This tracker maintains a small mutable affective vector per agent
({mood, energy, opinion_strength, fatigue}) that updates each round from the
interactions the agent *received* (likes/dislikes/replies) plus its own activity
(→ fatigue), and renders a compact one-line "current state" that the runner injects
into that agent's prompt for the round.

OPTIONAL-DEGRADE: only used when Config.SIM_AGENT_DYNAMICS is on (the runner skips
all of this otherwise → byte-identical static-persona behavior).

This module is deliberately pure (no camel/oasis/DB imports) so the update math and
state rendering are unit-tested offline. The OASIS-specific wiring (extracting
received-interaction signals from the round's actions and injecting the rendered
line into the agent's system message) lives in run_parallel_simulation.py.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


# OASIS action types → received-interaction valence (same target-name keys
# coalition_map relies on). Engagement (replies/comments) is attention without a
# clear sign; it still hardens opinion_strength but does not move mood.
POS_ACTIONS = {"LIKE_POST", "LIKE_COMMENT", "REPOST", "FOLLOW", "QUOTE_POST"}
NEG_ACTIONS = {"DISLIKE_POST", "DISLIKE_COMMENT", "MUTE"}
ENGAGE_ACTIONS = {"CREATE_COMMENT", "REPLY"}
# RUN-8: 'target_user_name' 是 _enrich_action_context 为 FOLLOW/MUTE 实际写入的键
# （它从不写 followee_name/quoted_author_name）——缺了它，FOLLOW（正向）与 MUTE（负向）
# 的被动信号永远解析不到目标 agent，动态情感更新静默丢失一整类信号。
TARGET_NAME_KEYS = ("post_author_name", "original_author_name", "comment_author_name",
                    "quoted_author_name", "followee_name", "target_name",
                    "target_user_name")


def extract_round_signals(actual_actions, name_to_id):
    """Pure: derive per-agent received-interaction signals + activity from a round's
    actions. received[aid]={pos,neg,engage} (interactions the agent RECEIVED);
    activity[aid]=number of actions the agent TOOK. Actions whose target can't be
    resolved to an agent id simply contribute no received signal (best-effort)."""
    received: Dict[int, Dict[str, int]] = {}
    activity: Dict[int, int] = {}
    name_to_id = name_to_id or {}
    for a in (actual_actions or []):
        if not isinstance(a, dict):
            continue
        aid = a.get("agent_id")
        if aid is not None:
            activity[aid] = activity.get(aid, 0) + 1
        at = (a.get("action_type") or "").upper()
        args = a.get("action_args") or {}
        tname = ""
        for k in TARGET_NAME_KEYS:
            v = str(args.get(k, "") or "").strip()
            if v:
                tname = v
                break
        if not tname:
            continue
        tid = name_to_id.get(tname)
        if tid is None:
            continue
        slot = received.setdefault(tid, {"pos": 0, "neg": 0, "engage": 0})
        if at in POS_ACTIONS:
            slot["pos"] += 1
        elif at in NEG_ACTIONS:
            slot["neg"] += 1
        elif at in ENGAGE_ACTIONS:
            slot["engage"] += 1
    return received, activity


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _squash(x: float) -> float:
    """Bounded monotonic squash into (-1, 1): keeps a single big round from
    saturating a state in one step."""
    return x / (1.0 + abs(x))


class AgentDynamicsTracker:
    """Mutable per-agent affective state across simulation rounds.

    State per agent: mood (-1..1), energy (0..1), opinion_strength (0..1),
    fatigue (0..1). Update rules are intentionally conservative + bounded; exact
    constants come from Config (SIM_DYNAMICS_*), overridable for tests.
    """

    def __init__(self, agent_ids: Iterable[int], *, initial: Optional[Dict[int, Dict[str, Any]]] = None,
                 mood_lr: float = 0.25, opinion_lr: float = 0.15,
                 fatigue_rate: float = 0.20, fatigue_decay: float = 0.10):
        self.mood_lr = float(mood_lr)
        self.opinion_lr = float(opinion_lr)
        self.fatigue_rate = float(fatigue_rate)
        self.fatigue_decay = float(fatigue_decay)
        self._state: Dict[int, Dict[str, float]] = {}
        initial = initial or {}
        for aid in agent_ids:
            seed = initial.get(aid, {}) if isinstance(initial, dict) else {}
            try:
                mood = _clamp(float(seed.get("sentiment_bias", 0.0) or 0.0), -1.0, 1.0)
            except (TypeError, ValueError):
                mood = 0.0
            infl = seed.get("influence", seed.get("influence_weight"))
            try:
                opinion = _clamp(float(infl), 0.0, 1.0) if infl is not None else 0.3
            except (TypeError, ValueError):
                opinion = 0.3
            self._state[aid] = {"mood": mood, "energy": 1.0,
                                "opinion_strength": opinion, "fatigue": 0.0}
        # QUALITY-OPT C6: track whether the dynamics engine actually fired. If no round ever
        # delivers a received-interaction signal (e.g. a hollow sim), the report must NOT claim
        # emotional/opinion EVOLUTION that never happened — dynamics_summary() exposes this.
        self._rounds_observed = 0
        self._rounds_with_received = 0

    @classmethod
    def from_config(cls, agent_configs: List[Dict[str, Any]], *, config_obj: Any = None) -> "AgentDynamicsTracker":
        """Build a tracker from the simulation config's agent_configs, seeding mood
        from sentiment_bias and opinion_strength from influence; learning rates from
        Config.SIM_DYNAMICS_* (or an injected config_obj for tests)."""
        if config_obj is None:
            from ..config import Config as config_obj  # type: ignore
        ids: List[int] = []
        initial: Dict[int, Dict[str, Any]] = {}
        for cfg in (agent_configs or []):
            if not isinstance(cfg, dict):
                continue
            aid = cfg.get("agent_id")
            if aid is None:
                continue
            ids.append(aid)
            initial[aid] = {
                "sentiment_bias": cfg.get("sentiment_bias"),
                "influence": cfg.get("influence", cfg.get("influence_weight")),
            }
        return cls(
            ids, initial=initial,
            mood_lr=getattr(config_obj, "SIM_DYNAMICS_MOOD_LR", 0.25),
            opinion_lr=getattr(config_obj, "SIM_DYNAMICS_OPINION_LR", 0.15),
            fatigue_rate=getattr(config_obj, "SIM_DYNAMICS_FATIGUE_RATE", 0.20),
            fatigue_decay=getattr(config_obj, "SIM_DYNAMICS_FATIGUE_DECAY", 0.10),
        )

    def observe_round(self, received: Dict[int, Dict[str, int]], activity: Dict[int, int]) -> None:
        """Update every agent's state from this round's signals.

        received[aid] = {"pos": n, "neg": n, "engage": n} (interactions the agent
        RECEIVED). activity[aid] = number of actions the agent took this round.
        mood follows net valence; any attention hardens opinion_strength; acting
        accrues fatigue (which decays each round); energy = 1 - fatigue.
        """
        received = received or {}
        activity = activity or {}
        self._rounds_observed += 1
        if any((isinstance(r, dict) and (r.get("pos") or r.get("neg") or r.get("engage")))
               for r in received.values()):
            self._rounds_with_received += 1
        for aid, st in self._state.items():
            r = received.get(aid) or {}
            pos = int(r.get("pos", 0) or 0)
            neg = int(r.get("neg", 0) or 0)
            eng = int(r.get("engage", 0) or 0)
            net = pos - neg
            total = pos + neg + eng
            st["mood"] = _clamp(st["mood"] + self.mood_lr * _squash(net), -1.0, 1.0)
            if total > 0:
                st["opinion_strength"] = _clamp(
                    st["opinion_strength"] + self.opinion_lr * _squash(total), 0.0, 1.0)
            acted = 1.0 if activity.get(aid, 0) else 0.0
            st["fatigue"] = _clamp(
                st["fatigue"] * (1.0 - self.fatigue_decay) + self.fatigue_rate * acted, 0.0, 1.0)
            st["energy"] = _clamp(1.0 - st["fatigue"], 0.0, 1.0)

    def dynamics_summary(self) -> Dict[str, Any]:
        """QUALITY-OPT C6: did the dynamics engine actually fire? ``active`` is False when no
        round delivered a received-interaction signal — the report must then NOT narrate
        emotional/opinion evolution. Consumed alongside simulation_health."""
        return {
            "rounds_observed": self._rounds_observed,
            "rounds_with_received_signal": self._rounds_with_received,
            "active": self._rounds_with_received > 0,
        }

    def get_state(self, aid: int) -> Dict[str, float]:
        return dict(self._state.get(aid, {}))

    def state_line(self, aid: int) -> str:
        """Compact one-line state for prompt injection. Returns '' when the agent is
        near baseline (so round-1 / unchanged agents inject nothing — no noise)."""
        st = self._state.get(aid)
        if not st:
            return ""
        parts: List[str] = []
        mood = st["mood"]
        if mood >= 0.4:
            parts.append("情绪亢奋/激动")
        elif mood <= -0.4:
            parts.append("情绪低落/愤懑")
        if st["opinion_strength"] >= 0.7:
            parts.append("立场已明显强化")
        if st["fatigue"] >= 0.6:
            parts.append("有些疲惫，参与意愿下降")
        if not parts:
            return ""
        return "【你当前状态】" + "；".join(parts) + "。让你的发帖语气、立场强度与参与度体现这一状态。"


# ============================================================================
# ITEM 20 — 模拟真实感纯函数（确定性、无 camel/oasis/DB 依赖，可离线单测）。
# 这些函数只做「算什么」，「怎样接入 OASIS env.step / run_summary」的副作用留在
# run_parallel_simulation.py 与 simulation_runner.py。四个修复各自 env 门控、默认开、
# 可降级：采样器/节流/比例侦测/小时记账。放在本纯模块以便两处复用且黄金测试可复现。
# ============================================================================

# 有机动作分类（与 simulation_runner._ORGANIC_TYPES 语义一致；此处按 post/comment/like
# 三桶拆分供比例侦测使用）。REPOST/QUOTE_POST 计为"帖"类扩散动作。
_POST_ACTIONS = {"CREATE_POST", "QUOTE_POST", "REPOST"}
_COMMENT_ACTIONS = {"CREATE_COMMENT"}
_LIKE_ACTIONS = {"LIKE_POST", "LIKE_COMMENT"}
# 反应类（点赞/点踩）统一归入侦测器的 "likes"（互动反应）桶——downvote 也是有机互动，
# 不计入会把「只有踩」的轮误报成塌缩。
_REACTION_ACTIONS = _LIKE_ACTIONS | {"DISLIKE_POST", "DISLIKE_COMMENT"}


def classify_organic_action(action_type: str) -> Optional[str]:
    """ITEM 20 (SIM_ORGANIC_RATIO_DETECTOR 辅助): 把动作类型归入 post/comment/like 三桶，
    供 detect_organic_ratio_collapse 计数。反应类（点赞+点踩）→ "likes"；其余（搜索/关注/
    do_nothing 等非内容动作）→ None（不计入比例）。纯函数、大小写无关。"""
    at = str(action_type or "").upper()
    if at in _POST_ACTIONS:
        return "posts"
    if at in _COMMENT_ACTIONS:
        return "comments"
    if at in _REACTION_ACTIONS:
        return "likes"
    return None


def sample_engagement_likes(
    post_weights: Dict[int, float],
    post_authors: Dict[int, int],
    liker_ids: Iterable[int],
    rate: float,
    rng: Any,
) -> List["tuple"]:
    """ITEM 20 (SIM_ENGAGEMENT_SAMPLER): 每轮有机动作后，从本轮活跃 agent 中按概率 ``rate``
    确定性采样点赞/顶帖动作，落在本轮新帖上，权重∝帖子已获互动（winner-take-all 真实感——
    OASIS 默认 feed 常见 500 帖 0 赞的纯广播，需补足被动互动这一层）。

    纯函数：给定同一 ``rng`` 状态与输入必得同一结果（黄金测试可复现）。副作用（构造
    ManualAction(LIKE_POST) 并 env.step）留在 runner。

    Args:
      post_weights: {post_id: weight}  本轮候选帖 → 互动权重（调用方传 1 + 本轮该帖收到的评论/赞数）；
                    weight<=0 的帖被排除（不产生点赞）。
      post_authors: {post_id: author_agent_id}  用于避免自赞（agent 不给自己的帖点赞）。
      liker_ids:    候选点赞者 agent_id 可迭代（本轮活跃者）。
      rate:         [0,1]，每个活跃者本轮产生一次点赞的概率（clamp 到 [0,1]）。
      rng:          random.Random（种子化→可复现）。
    Returns:
      按 (liker, post_id) 升序排序的去重列表；每个 agent 至多产生一次点赞；不赞自己的帖。
      无候选帖 / rate<=0 / 无点赞者 → []。
    """
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return []
    if rate <= 0.0:
        return []
    if rate > 1.0:
        rate = 1.0
    # 只保留正权重帖，按 post_id 稳定排序（确定性）。
    posts = sorted(pid for pid, w in post_weights.items() if (w or 0) > 0)
    if not posts:
        return []
    likers = sorted(set(int(a) for a in liker_ids))
    if not likers:
        return []

    out: List[tuple] = []
    for liker in likers:
        # 先抽 rate 门限——无论后续是否有可赞帖都消费一次 rng，保证序列稳定。
        if rng.random() >= rate:
            continue
        cand = [pid for pid in posts if int(post_authors.get(pid, -1)) != liker]
        if not cand:
            continue
        weights = [float(post_weights.get(pid, 0.0)) for pid in cand]
        total = sum(weights)
        if total <= 0:
            continue
        u = rng.random() * total
        acc = 0.0
        chosen = cand[-1]  # 数值边界兜底（浮点误差时取最后一个）
        for pid, w in zip(cand, weights):
            acc += w
            if u <= acc:
                chosen = pid
                break
        out.append((liker, chosen))
    return sorted(out)


def throttle_seed_follows(
    by_follower: Dict[int, List[int]],
    cap: int,
) -> "tuple":
    """ITEM 20 (SIM_MAX_FOLLOWS_PER_AGENT_ROUND): 每个 follower 的种子 FOLLOW **动作**上限。
    630 条种子关注（初始社交图）在 round-0 一次性灌入，早期动作日志被 FOLLOW 淹没（其余动作
    看不见）。这里只截断 FOLLOW 动作列表（写 trace/follow 表的那一层），不触碰调用方已对
    全量边执行的 ``agent_graph.add_edge``——图拓扑仍完整，只是不再对 trace 制造风暴。

    纯函数。Args: by_follower={follower: [followee,...]}；cap<=0 表示不限（旧行为）。
    Returns: (capped_by_follower, dropped_count)。capped 为新 dict（不改入参）。
    """
    if cap is None or cap <= 0:
        return {k: list(v) for k, v in by_follower.items()}, 0
    out: Dict[int, List[int]] = {}
    dropped = 0
    for follower, followees in by_follower.items():
        if len(followees) > cap:
            dropped += len(followees) - cap
            out[follower] = list(followees[:cap])
        else:
            out[follower] = list(followees)
    return out, dropped


def detect_organic_ratio_collapse(
    platform_round_counts: Dict[str, Dict[int, Dict[str, int]]],
    min_consecutive: int,
) -> List[Dict[str, Any]]:
    """ITEM 20 (SIM_ORGANIC_RATIO_DETECTOR): 逐平台逐轮跟踪 post:comment:like 比例；当连续
    ≥K 轮出现「posts>0 而 comments+likes==0」时记结构化告警。**检测+诚实优先**——只报告
    塌缩，绝不伪造互动来粉饰（run_summary 的健康门/报告 caveat 据此下调可信度）。

    纯函数。Args:
      platform_round_counts: {platform: {round_num: {"posts":int,"comments":int,"likes":int}}}
      min_consecutive: K（<1 视作 1）。
    Returns:
      告警列表（按 platform、start_round 升序），每条
      {"platform","start_round","end_round","rounds","detail"}。无塌缩→[]。
    """
    if min_consecutive is None or min_consecutive < 1:
        min_consecutive = 1
    warnings: List[Dict[str, Any]] = []
    for platform in sorted(platform_round_counts.keys()):
        rounds_map = platform_round_counts[platform] or {}
        # 用显式的"连续塌缩轮"列表而非轮次跨度，避免死轮（无任何动作的轮次，
        # rounds_map 里根本没有该键）被误当作打断或延长连续段。
        run_rounds: List[int] = []
        for rnd in sorted(rounds_map.keys()):
            c = rounds_map[rnd] or {}
            posts = int(c.get("posts", 0) or 0)
            engaged = int(c.get("comments", 0) or 0) + int(c.get("likes", 0) or 0)
            collapsed = posts > 0 and engaged == 0
            if collapsed:
                run_rounds.append(rnd)
            else:
                if len(run_rounds) >= min_consecutive:
                    warnings.append({
                        "platform": platform,
                        "start_round": run_rounds[0],
                        "end_round": run_rounds[-1],
                        "rounds": len(run_rounds),
                        "detail": (f"{len(run_rounds)} 连续轮 posts>0 但 comments+likes==0 "
                                   f"（轮 {run_rounds[0]}–{run_rounds[-1]}）：有机互动塌缩，"
                                   f"该平台样本不得叙述为活跃讨论"),
                    })
                run_rounds = []
        if len(run_rounds) >= min_consecutive:
            warnings.append({
                "platform": platform,
                "start_round": run_rounds[0],
                "end_round": run_rounds[-1],
                "rounds": len(run_rounds),
                "detail": (f"{len(run_rounds)} 连续轮 posts>0 但 comments+likes==0 "
                           f"（轮 {run_rounds[0]}–{run_rounds[-1]}）：有机互动塌缩，"
                           f"该平台样本不得叙述为活跃讨论"),
            })
    return warnings


def simulated_hours_from_rounds(rounds_executed: int, minutes_per_round: float) -> float:
    """ITEM 20 (SIMULATED_HOURS 记账): run_summary 的 simulated_hours 必须等于
    rounds_executed × minutes_per_round / 60。历史上该字段挂在 state 上却常年不增（round_end
    事件的 simulated_hours 未被可靠回传到 run_summary），报告读到恒 0 的模拟时长。这里给出
    唯一权威公式，供 write_run_summary 直接从已算出的 rounds_executed 重算。

    纯函数。非法/缺失输入 → 0.0（降级安全）。
    """
    try:
        r = int(rounds_executed)
        mpr = float(minutes_per_round)
    except (TypeError, ValueError):
        return 0.0
    if r <= 0 or mpr <= 0:
        return 0.0
    return round(r * mpr / 60.0, 2)
