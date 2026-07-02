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
