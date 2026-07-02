"""NEXTSTEPS P1-1: a modeled outcome WorldState the simulation converges ON.

The forecast outcome is the aggregate of consequential **decisions** (allocate,
commit, vote, switch supplier), not of posts. Today OASIS produces post-counts and
``final_stance_share`` is voice-share — the forecaster then has to make the unmodeled
leap from "who talked most" to a number. This module is the pure, testable core of
the fix: a base-rate-seeded distribution over the forecast scenarios, evolved each
round by **resource-weighted** agent commitments, with EWMA convergence tracking.

Kept pure / side-effect-free (no camel/oasis/LLM imports) so it is unit-testable
offline, exactly like ``agent_dynamics.py``. The OASIS round-loop wiring (eliciting
a per-agent ``{scenario, magnitude, confidence}`` decision and persisting
``decisions.jsonl`` / ``world_state_trajectory.json``) is gated behind
``SIM_DECISION_CHANNEL`` (default OFF) and lives in ``run_parallel_simulation.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _norm_shares(shares: Dict[str, float]) -> Dict[str, float]:
    """Clamp negatives to 0 and renormalize to sum 1; empty/degenerate → uniform."""
    if not shares:
        return {}
    total = sum(max(0.0, float(v)) for v in shares.values())
    if total <= 0:
        n = len(shares)
        return {k: round(1.0 / n, 6) for k in shares}
    return {k: round(max(0.0, float(v)) / total, 6) for k, v in shares.items()}


def commitments_from_decisions(
    decisions: List[Dict[str, Any]],
    weight_by_agent: Optional[Dict[Any, float]] = None,
) -> List[Dict[str, Any]]:
    """Turn raw per-agent decisions into resource-weighted commitments for ``step``.

    Each decision: ``{agent_id, scenario, magnitude, confidence}``. The commitment
    weight = (outcome power on the result, default 1.0) × confidence. Pure.

    R2-SIM-2: the weight that moves the modeled OUTCOME is the agent's *outcome power*
    (structural/resource leverage over the result), NOT its voice/activation influence.
    Resolution order per decision: explicit ``outcome_power`` on the decision →
    ``weight_by_agent[agent_id]`` (a power map the caller may still pass) → 1.0. An empty
    / abstaining ``scenario`` contributes nothing (R2-CAL-13/SIM-9 abstention).
    """
    out: List[Dict[str, Any]] = []
    wmap = weight_by_agent or {}
    for d in decisions or []:
        if not isinstance(d, dict):
            continue
        sc = str(d.get("scenario") or "").strip()
        if not sc:
            continue
        try:
            mag = max(0.0, float(d.get("magnitude", 1.0) or 0.0))
            conf = float(d.get("confidence", 1.0) or 0.0)
        except (TypeError, ValueError):
            continue
        conf = max(0.0, min(1.0, conf))
        # R2-SIM-2: prefer per-decision outcome_power; fall back to the supplied map.
        power = d.get("outcome_power")
        if power is None:
            power = wmap.get(d.get("agent_id"), 1.0)
        try:
            base_w = max(0.0, float(power))
        except (TypeError, ValueError):
            base_w = 1.0
        out.append({"scenario": sc, "magnitude": mag, "weight": base_w * conf})
    return out


class WorldState:
    """A distribution over scenario names ∈ [0,1] summing to 1, evolved by commitments.

    ``inertia`` ∈ [0,1] is how much of the prior persists each round (high = slow,
    stable; low = fast, jumpy). Seeded from ``base_rates`` (the outside view); absent
    base rates → uniform.
    """

    def __init__(self, scenarios: List[str], base_rates: Optional[Dict[str, float]] = None,
                 inertia: float = 0.7):
        self.scenarios = [str(s) for s in (scenarios or []) if str(s).strip()]
        self.inertia = max(0.0, min(1.0, float(inertia)))
        seed: Dict[str, float] = {}
        for s in self.scenarios:
            try:
                seed[s] = float((base_rates or {}).get(s, 0.0) or 0.0)
            except (TypeError, ValueError):
                seed[s] = 0.0
        # R2-CAL-13/SIM-9: distinguish a genuinely seeded prior from a fabricated
        # uniform one so a forecaster can flag "uniform_prior" instead of silently
        # treating 50/50 as evidence.
        self.uniform_prior = sum(seed.values()) <= 0
        if self.uniform_prior:  # no usable base rates → uniform prior
            seed = dict.fromkeys(self.scenarios, 1.0)
        self.shares = _norm_shares(seed)
        self.history: List[Dict[str, float]] = [dict(self.shares)]
        self._last_delta = 1.0
        self._ewma_delta = 1.0
        # P1-4: round at which the trajectory settled (set by the orchestration loop).
        self.converged_at: Optional[int] = None

    def step(self, commitments: List[Dict[str, Any]],
             inertia: Optional[float] = None) -> Dict[str, float]:
        """Evolve one round: aggregate resource-weighted commitments into a target
        distribution, blend with the prior by ``inertia``. No commitments → unchanged.

        R2-SIM-12: ``inertia`` may be overridden per round (e.g. scaled by the calendar
        gap between rounds) — ``None`` keeps the instance default so existing callers
        and the no-date path are byte-identical.
        """
        if not self.scenarios:
            return {}
        eff_inertia = self.inertia if inertia is None else max(0.0, min(1.0, float(inertia)))
        votes: Dict[str, float] = dict.fromkeys(self.scenarios, 0.0)
        for c in commitments or []:
            if not isinstance(c, dict):
                continue
            sc = str(c.get("scenario") or "")
            if sc not in votes:
                continue
            try:
                votes[sc] += max(0.0, float(c.get("magnitude", 1.0) or 0.0)) * \
                    max(0.0, float(c.get("weight", 1.0) or 0.0))
            except (TypeError, ValueError):
                continue
        if sum(votes.values()) > 0:
            target = _norm_shares(votes)
            blended = {s: eff_inertia * self.shares[s] + (1.0 - eff_inertia) * target[s]
                       for s in self.scenarios}
            new = _norm_shares(blended)
        else:
            new = dict(self.shares)
        delta = sum(abs(new[s] - self.shares[s]) for s in self.scenarios)
        self._last_delta = round(delta, 6)
        self._ewma_delta = round(0.5 * self._ewma_delta + 0.5 * delta, 6)
        self.shares = new
        self.history.append(dict(self.shares))
        return self.shares

    def converged(self, eps: float = 0.02) -> bool:
        """True once the EWMA of per-round L1 change drops below ``eps`` (settled)."""
        return self._ewma_delta < float(eps)

    def outcome(self) -> Dict[str, Any]:
        """The modeled outcome: scenario shares + leader + convergence diagnostics.
        This is what a forecaster reads INSTEAD of voice-share ``final_stance_share``.
        """
        ranked = sorted(self.shares.items(), key=lambda kv: -kv[1])
        return {
            "shares": dict(self.shares),
            "leader": ranked[0][0] if ranked else None,
            "leader_share": round(ranked[0][1], 4) if ranked else 0.0,
            "rounds": max(0, len(self.history) - 1),
            "ewma_delta": self._ewma_delta,
            "converged": self.converged(),
            "converged_at": self.converged_at,   # P1-4 (set by the loop; None until settled)
            "uniform_prior": self.uniform_prior,  # R2-CAL-13/SIM-9
        }
