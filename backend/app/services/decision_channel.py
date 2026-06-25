"""NEXTSTEPS P1-1 (integration): evolve the outcome WorldState from agent decisions.

The pure modeling core is ``services.worldstate.WorldState``. This module is the
orchestration that turns a finished simulation into a modeled-outcome trajectory: for
each round it elicits, in one batched structured LLM call, where each active agent
*committed* (toward which forecast scenario, with what magnitude/confidence), weights
those commitments by the agent's influence/resources, and steps the WorldState. The
final ``outcome()`` is what a forecaster reads INSTEAD of voice-share ``final_stance_share``.

Run **post-simulation** (after both platforms finish) so there is a single shared
WorldState and zero coupling to the concurrent per-platform round loops — gated behind
``SIM_DECISION_CHANNEL`` (default OFF). The orchestration is pure given (actions, llm),
so it is unit-testable offline with a fake LLM (the per-round elicitation is the only
LLM touch). NEXTSTEPS P1-2: each round's readout is stamped with its mapped calendar
date when a horizon is supplied; P1-4: convergence (time-to-settle) is recorded.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .worldstate import WorldState, commitments_from_decisions


def _build_round_decision_prompt(scenarios: List[str], active: List[Dict[str, Any]],
                                 round_num: int, as_of: Optional[str]) -> str:
    sc_list = "、".join(scenarios)
    roster = "\n".join(
        f"- id={a.get('agent_id')} {a.get('name', '')}（立场：{a.get('stance', '?')}，"
        f"影响力：{a.get('influence', '?')}）"
        for a in active[:60]
    )
    when = f"（对应时点约 {as_of}）" if as_of else ""
    return (
        f"这是一个预测推演。候选**互斥**情景：{sc_list}。\n"
        f"第 {round_num} 轮{when}的活跃角色会基于其立场/利益，对**结果**做出本轮的实质承诺"
        "（投票/下单/站队/分配，而非单纯发言）。为每个角色判断它本轮最倾向促成**哪一个**情景，"
        "以及力度 magnitude(0-1) 与信心 confidence(0-1)。\n"
        "只输出 JSON（不要解释）："
        '{"decisions": [{"agent_id": <int>, "scenario": "<必须取自上面候选>", '
        '"magnitude": 0-1, "confidence": 0-1}]}\n'
        f"角色名册：\n{roster}\n"
    )


def _elicit_round_decisions(llm, scenarios: List[str], active: List[Dict[str, Any]],
                            round_num: int, as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """One batched structured call assigning each active agent a scenario commitment.

    Returns ``[{agent_id, scenario, magnitude, confidence}]`` (scenario validated against
    the candidate set). Degrade-safe: empty inputs or any failure → ``[]``.
    """
    if not active or not scenarios:
        return []
    try:
        raw = llm.chat_json(
            messages=[{"role": "user",
                       "content": _build_round_decision_prompt(scenarios, active, round_num, as_of)}],
            temperature=0.2,
            max_tokens=2048,
        )
    except Exception:  # noqa: BLE001 — 一轮 elicit 失败不拖垮整条决策通道
        return []
    decs = raw.get("decisions") if isinstance(raw, dict) else None
    out: List[Dict[str, Any]] = []
    valid = set(scenarios)
    for d in (decs or []):
        if not isinstance(d, dict):
            continue
        sc = str(d.get("scenario") or "").strip()
        if sc not in valid:
            continue
        out.append({
            "agent_id": d.get("agent_id"),
            "scenario": sc,
            "magnitude": d.get("magnitude", 1.0),
            "confidence": d.get("confidence", 0.7),
        })
    return out


def _agent_weight_map(agent_configs: Optional[List[Dict[str, Any]]]) -> Dict[Any, float]:
    """{agent_id: influence_weight} for resource-weighting commitments."""
    out: Dict[Any, float] = {}
    for c in (agent_configs or []):
        if not isinstance(c, dict):
            continue
        aid = c.get("agent_id")
        try:
            out[aid] = max(0.0, float(c.get("influence_weight", 1.0) or 1.0))
        except (TypeError, ValueError):
            out[aid] = 1.0
    return out


def _agent_meta_map(agent_configs: Optional[List[Dict[str, Any]]]) -> Dict[Any, Dict[str, Any]]:
    out: Dict[Any, Dict[str, Any]] = {}
    for c in (agent_configs or []):
        if isinstance(c, dict) and c.get("agent_id") is not None:
            out[c["agent_id"]] = {
                "agent_id": c.get("agent_id"),
                "name": c.get("entity_name") or c.get("name") or "",
                "stance": c.get("stance", ""),
                "influence": c.get("influence", c.get("influence_weight", "")),
            }
    return out


def run_decision_channel(
    actions: List[Dict[str, Any]],
    agent_configs: Optional[List[Dict[str, Any]]],
    seed: Dict[str, Any],
    llm,
    *,
    inertia: float = 0.7,
    conv_eps: float = 0.02,
    round_to_date=None,
    max_active_per_round: int = 60,
) -> Dict[str, Any]:
    """Replay the simulation rounds → evolve a single WorldState → modeled outcome.

    ``actions``: list of ``{round, agent_id, agent_name}`` (the action log). ``seed``:
    ``{scenarios, base_rates}`` (from ``actors.world_state_seed_from_actors``). ``llm``
    drives the per-round batched elicitation (``chat_json``). ``round_to_date`` maps a
    1-based round int → ISO date (P1-2). Returns
    ``{outcome, trajectory, decisions, converged_at, n_rounds}``; empty seed → ``{}``.
    """
    scenarios = [str(s) for s in (seed or {}).get("scenarios", []) if str(s).strip()]
    if not scenarios:
        return {}
    ws = WorldState(scenarios, (seed or {}).get("base_rates"), inertia=inertia)
    weights = _agent_weight_map(agent_configs)
    meta = _agent_meta_map(agent_configs)

    by_round: Dict[int, "Dict[Any, Dict[str, Any]]"] = {}
    for a in (actions or []):
        if not isinstance(a, dict):
            continue
        try:
            rnd = int(a.get("round", a.get("round_num", 0)) or 0)
        except (TypeError, ValueError):
            continue
        aid = a.get("agent_id")
        if aid is None:
            continue
        roster = by_round.setdefault(rnd, {})
        if aid not in roster:
            roster[aid] = meta.get(aid, {"agent_id": aid, "name": a.get("agent_name", ""),
                                         "stance": "", "influence": ""})

    trajectory: List[Dict[str, Any]] = [{"round": 0, **ws.outcome()}]
    all_decisions: List[Dict[str, Any]] = []
    converged_at: Optional[int] = None
    for rnd in sorted(r for r in by_round if r >= 1):
        active = list(by_round[rnd].values())[:max_active_per_round]
        as_of = None
        if round_to_date is not None:
            try:
                as_of = round_to_date(rnd)
            except Exception:  # noqa: BLE001
                as_of = None
        decisions = _elicit_round_decisions(llm, scenarios, active, rnd, as_of)
        for d in decisions:
            d["round"] = rnd
        all_decisions.extend(decisions)
        ws.step(commitments_from_decisions(decisions, weights))
        snap = {"round": rnd, **ws.outcome()}
        if as_of:
            snap["as_of"] = as_of
        trajectory.append(snap)
        if converged_at is None and ws.converged(conv_eps) and rnd >= 2:
            converged_at = rnd

    out = ws.outcome()
    out["converged_at"] = converged_at
    return {
        "outcome": out,
        "trajectory": trajectory,
        "decisions": all_decisions,
        "converged_at": converged_at,
        "n_rounds": max((r for r in by_round if r >= 1), default=0),
        "scenarios": scenarios,
        "schema_version": 1,
    }
