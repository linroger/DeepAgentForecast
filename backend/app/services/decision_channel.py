"""NEXTSTEPS P1-1 (integration): evolve the outcome WorldState from agent decisions.

The pure modeling core is ``services.worldstate.WorldState``. This module is the
orchestration that turns a finished simulation into a modeled-outcome trajectory: for
each round it elicits, in one batched structured LLM call, where each active agent
*committed* (toward which forecast scenario, with what magnitude/confidence), weights
those commitments by the agent's **outcome power** (resource/structural leverage on the
result, NOT voice), and steps the WorldState. The final ``outcome()`` is what a
forecaster reads INSTEAD of voice-share ``final_stance_share``.

Run **post-simulation** (after both platforms finish) so there is a single shared
WorldState and zero coupling to the concurrent per-platform round loops — gated behind
``SIM_DECISION_CHANNEL`` (default OFF). The orchestration is pure given (actions, llm),
so it is unit-testable offline with a fake LLM (the per-round elicitation is the only
LLM touch).

Round elicitation is a **pure function of the frozen action log** (it sees the round
roster + the immutable *base* distribution, never the evolving shares), which makes it
safe to fan the rounds out in parallel under a bounded thread pool (SIM-6 / R2-EXEC-8)
and to de-duplicate identical rosters via a cache (R2-EXEC-10). The WorldState is then
``step``-ed strictly in round order during a cheap, serial replay.

Findings landed here: R2-SIM-1 (inject round posts + base WorldState shares + affect),
R2-SIM-2 (power-weighted commitments + influence kept for activation), R2-SIM-3 (feed
gains_if/loses_if incentives), SIM-5 (power-sort + DECISION_CHANNEL_MAX_ACTIVE cap),
SIM-6 / R2-EXEC-8 (parallel two-phase elicit→replay), R2-EXEC-10 (collapse the audience
tail into one weighted public block + per-roster cache), R2-SIM-12 (calendar-scaled
inertia), SIM-1 (windowed convergence early-stop signal), abstention (R2-CAL-13/SIM-9).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .worldstate import WorldState, commitments_from_decisions

# Synthetic roster id for the collapsed audience tail (R2-EXEC-10). A string id never
# collides with the integer agent ids OASIS emits.
PUBLIC_BLOCK_ID = "__public__"
# Literal an agent may emit to opt out of committing this round (R2-CAL-13/SIM-9). It is
# intentionally NOT in the candidate scenario set, so it is filtered to a no-op weight.
ABSTAIN_TOKEN = "弃权"


def _cfg(name: str, default: Any) -> Any:
    """Read a Config flag degrade-safely (config.py is owned by another bucket)."""
    try:
        from ..config import Config  # local import keeps this module import-light/testable
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001 — never let config wiring break the channel
        return default


def _render_incentives(meta: Dict[str, Any]) -> str:
    """R2-SIM-3: compact gains_if/loses_if hint so commitments follow incentives."""
    gains = str(meta.get("gains_if") or "").strip()
    loses = str(meta.get("loses_if") or "").strip()
    bits = []
    if gains:
        bits.append(f"利好于：{gains}")
    if loses:
        bits.append(f"受损于：{loses}")
    return ("；".join(bits)) if bits else ""


def _render_roster_line(a: Dict[str, Any]) -> str:
    """One roster line, optionally enriched with this round's posts + affect (R2-SIM-1)
    and the agent's incentives (R2-SIM-3). All extras are best-effort: absent → omitted,
    so the line degrades to the original ``id/name/stance/influence`` form."""
    base = (f"- id={a.get('agent_id')} {a.get('name', '')}（立场：{a.get('stance', '?')}，"
            f"影响力：{a.get('influence', '?')}）")
    extras: List[str] = []
    inc = _render_incentives(a)
    if inc:
        extras.append(inc)
    affect = str(a.get("affect") or "").strip()
    if affect:
        extras.append(f"当前状态：{affect}")
    post = str(a.get("post") or "").strip()
    if post:
        snippet = post.replace("\n", " ")[:120]
        extras.append(f"本轮发声：{snippet}")
    if extras:
        base += "｜" + "；".join(extras)
    return base


def _build_round_decision_prompt(scenarios: List[str], active: List[Dict[str, Any]],
                                 round_num: int, as_of: Optional[str],
                                 base_shares: Optional[Dict[str, float]] = None,
                                 abstain_allowed: bool = True) -> str:
    sc_list = "、".join(scenarios)
    roster = "\n".join(_render_roster_line(a) for a in active)
    when = f"（对应时点约 {as_of}）" if as_of else ""
    # R2-SIM-1: anchor on the modeled base distribution (the seeded prior). It is the
    # immutable reference for every round (NOT the evolving per-round shares), which is
    # what keeps elicitation a pure, parallel-safe function of the frozen log.
    base_line = ""
    if base_shares:
        try:
            ordered = sorted(base_shares.items(), key=lambda kv: -float(kv[1]))
            base_line = ("当前建模基线分布（外部视角，仅供参考，不要机械照抄）："
                         + "，".join(f"{k}={float(v):.0%}" for k, v in ordered) + "。\n")
        except Exception:  # noqa: BLE001
            base_line = ""
    abstain_line = (
        f'若某角色本轮在结果上没有实质利害或无法判断，可让它选择 "{ABSTAIN_TOKEN}"（不计入）。\n'
        if abstain_allowed else "")
    return (
        f"这是一个预测推演。候选**互斥**情景：{sc_list}。\n"
        f"{base_line}"
        f"第 {round_num} 轮{when}的活跃角色会基于其立场/利益，对**结果**做出本轮的实质承诺"
        "（投票/下单/站队/分配，而非单纯发言）。为每个角色判断它本轮最倾向促成**哪一个**情景，"
        "以及力度 magnitude(0-1) 与信心 confidence(0-1)。\n"
        f"{abstain_line}"
        "只输出 JSON（不要解释）："
        '{"decisions": [{"agent_id": <id>, "scenario": "<必须取自上面候选>", '
        '"magnitude": 0-1, "confidence": 0-1}]}\n'
        f"角色名册：\n{roster}\n"
    )


def _elicit_round_decisions(llm, scenarios: List[str], active: List[Dict[str, Any]],
                            round_num: int, as_of: Optional[str] = None,
                            base_shares: Optional[Dict[str, float]] = None,
                            abstain_allowed: bool = True) -> List[Dict[str, Any]]:
    """One batched structured call assigning each active agent a scenario commitment.

    Returns ``[{agent_id, scenario, magnitude, confidence}]`` (scenario validated against
    the candidate set; ``ABSTAIN_TOKEN`` and any other non-candidate value are dropped).
    Degrade-safe: empty inputs or any failure → ``[]``.
    """
    if not active or not scenarios:
        return []
    try:
        raw = llm.chat_json(
            messages=[{"role": "user",
                       "content": _build_round_decision_prompt(
                           scenarios, active, round_num, as_of, base_shares, abstain_allowed)}],
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
        if sc not in valid:  # ABSTAIN_TOKEN / invalid → no commitment (R2-CAL-13/SIM-9)
            continue
        out.append({
            "agent_id": d.get("agent_id"),
            "scenario": sc,
            "magnitude": d.get("magnitude", 1.0),
            "confidence": d.get("confidence", 0.7),
        })
    return out


def _to_weight(v: Any, default: float = 1.0) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return default


def _activation_weight_map(agent_configs: Optional[List[Dict[str, Any]]]) -> Dict[Any, float]:
    """{agent_id: influence_weight} — drives *activation*/roster ranking (who is loud
    enough to act), kept distinct from outcome power per R2-SIM-2."""
    out: Dict[Any, float] = {}
    for c in (agent_configs or []):
        if isinstance(c, dict) and c.get("agent_id") is not None:
            out[c["agent_id"]] = _to_weight(c.get("influence_weight", 1.0))
    return out


def _outcome_power_map(agent_configs: Optional[List[Dict[str, Any]]]) -> Dict[Any, float]:
    """{agent_id: outcome_power} — drives how much a commitment moves the OUTCOME.

    R2-SIM-2: defaults to ``influence_weight`` so today's behavior is preserved, but a
    config may carry an explicit ``outcome_power`` (e.g. structural/resource leverage)
    that differs from its voice/activation influence.
    """
    out: Dict[Any, float] = {}
    for c in (agent_configs or []):
        if not isinstance(c, dict) or c.get("agent_id") is None:
            continue
        power = c.get("outcome_power")
        if power is None:
            power = c.get("influence_weight", 1.0)
        out[c["agent_id"]] = _to_weight(power)
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
                # R2-SIM-3: incentives drive which scenario a rational agent commits to.
                "gains_if": c.get("gains_if", ""),
                "loses_if": c.get("loses_if", ""),
            }
    return out


def _build_active_roster(entries: List[Dict[str, Any]],
                         activation: Dict[Any, float],
                         power: Dict[Any, float],
                         cap: int) -> List[Dict[str, Any]]:
    """SIM-5 + R2-EXEC-10: rank the round's actors by *activation* (influence) and keep
    the top ``cap`` individually; collapse the remaining tail into ONE weighted public
    block whose outcome power is the sum of the tail's (so the silent-majority signal is
    aggregated, never silently dropped by truncation).
    """
    enriched = []
    for e in entries:
        aid = e.get("agent_id")
        ee = dict(e)
        ee["_act"] = activation.get(aid, _to_weight(e.get("influence"), 1.0))
        ee["outcome_power"] = power.get(aid, ee["_act"])
        enriched.append(ee)
    enriched.sort(key=lambda x: -float(x.get("_act", 0.0)))
    if cap <= 0 or len(enriched) <= cap:
        head, tail = enriched, []
    else:
        head, tail = enriched[:cap], enriched[cap:]
    roster = [{k: v for k, v in e.items() if k != "_act"} for e in head]
    if tail:
        tail_power = sum(float(e.get("outcome_power", 0.0)) for e in tail)
        roster.append({
            "agent_id": PUBLIC_BLOCK_ID,
            "name": f"其他公众（{len(tail)} 名沉默多数的合并代表）",
            "stance": "mixed",
            "influence": round(tail_power, 4),
            "outcome_power": tail_power,
        })
    return roster


def _roster_signature(active: List[Dict[str, Any]]) -> Tuple:
    """Stable key for caching identical rosters across rounds (R2-EXEC-10). The decision
    depends only on who is in the roster and their stance/incentives — not the round
    number — so a stable roster reuses one elicitation."""
    return tuple(sorted(
        (str(a.get("agent_id")), str(a.get("stance", "")),
         str(a.get("gains_if", "")), str(a.get("loses_if", "")),
         str(a.get("post", "")))
        for a in active
    ))


def _safe_round_to_date(round_to_date, rnd: int) -> Optional[str]:
    if round_to_date is None:
        return None
    try:
        v = round_to_date(rnd)
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except (TypeError, ValueError):
        return None


def _inertia_for_gap(base: float, prev_s: Optional[str], cur_s: Optional[str],
                     avg_gap_days: float) -> Optional[float]:
    """R2-SIM-12: scale per-round inertia by the calendar gap. Persistence over one
    *average* step is ``base``; over a gap of ``g`` days it is ``base ** (g/avg)`` so a
    longer real-time gap lets more change through (lower inertia) and a shorter one holds
    more. Clamped to [0.3, 0.95]. Returns ``None`` (→ use ``base``) whenever dates are
    missing or ``base`` is degenerate, so the no-date path is unchanged.
    """
    if not (0.0 < base < 1.0) or avg_gap_days <= 0:
        return None
    pd, cd = _parse_date(prev_s), _parse_date(cur_s)
    if pd is None or cd is None:
        return None
    gap = (cd - pd).days
    if gap < 0:
        return None
    eff = base ** (gap / avg_gap_days)
    return max(0.3, min(0.95, eff))


def _avg_gap_days(seed: Dict[str, Any], as_of_by_round: Dict[int, Optional[str]]) -> float:
    """Average calendar days per round across the run, for R2-SIM-12 normalization."""
    dated = [(_parse_date(seed.get("as_of_date")), 0)]
    dated += [(_parse_date(v), r) for r, v in as_of_by_round.items()]
    pts = [(d, r) for d, r in dated if d is not None]
    if len(pts) < 2:
        return 0.0
    pts.sort(key=lambda x: x[1])
    span = (pts[-1][0] - pts[0][0]).days
    rounds = pts[-1][1] - pts[0][1]
    return (span / rounds) if (span > 0 and rounds > 0) else 0.0


def _fan_out_elicit(llm, scenarios: List[str],
                    tasks: Dict[Any, Tuple[List[Dict[str, Any]], int, Optional[str]]],
                    base_shares: Optional[Dict[str, float]],
                    abstain_allowed: bool, concurrency: int) -> Dict[Any, List[Dict[str, Any]]]:
    """SIM-6 / R2-EXEC-8: run one elicitation per *unique* roster key, in parallel under
    a bounded pool. Returns ``{key: decisions}``. Each task is independent (pure over the
    frozen log) so ordering does not matter; the WorldState is stepped later in round
    order. A failing task degrades to ``[]`` for that key only.
    """
    results: Dict[Any, List[Dict[str, Any]]] = {}
    if not tasks:
        return results

    def _run(key):
        active, rnd, as_of = tasks[key]
        return key, _elicit_round_decisions(
            llm, scenarios, active, rnd, as_of, base_shares, abstain_allowed)

    workers = max(1, min(int(concurrency or 1), len(tasks)))
    if workers == 1:
        for key in tasks:
            _, decs = _run(key)
            results[key] = decs
        return results
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="decision-elicit") as ex:
        for key, decs in ex.map(_run, list(tasks.keys())):
            results[key] = decs
    return results


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
    concurrency: Optional[int] = None,
    abstain_allowed: bool = True,
    posts_by_round: Optional[Dict[int, Dict[Any, str]]] = None,
    affect_by_agent: Optional[Dict[Any, str]] = None,
) -> Dict[str, Any]:
    """Replay the simulation rounds → evolve a single WorldState → modeled outcome.

    ``actions``: list of ``{round, agent_id, agent_name}`` (the action log; each item may
    also carry ``post``/``text`` for R2-SIM-1). ``seed``: ``{scenarios, base_rates,
    as_of_date?, horizon_date?}`` (from ``actors.world_state_seed_from_actors``). ``llm``
    drives the per-round batched elicitation (``chat_json``). ``round_to_date`` maps a
    1-based round int → ISO date (P1-2). ``posts_by_round[round][agent_id]`` and
    ``affect_by_agent[agent_id]`` optionally enrich the prompt (R2-SIM-1). Returns
    ``{outcome, trajectory, decisions, converged_at, n_rounds, ...}``; empty seed → ``{}``.
    """
    scenarios = [str(s) for s in (seed or {}).get("scenarios", []) if str(s).strip()]
    if not scenarios:
        return {}
    seed = seed or {}
    ws = WorldState(scenarios, seed.get("base_rates"), inertia=inertia)
    base_shares = dict(ws.shares)  # immutable reference distribution for every round (R2-SIM-1)
    activation = _activation_weight_map(agent_configs)
    power = _outcome_power_map(agent_configs)
    meta = _agent_meta_map(agent_configs)
    posts_by_round = posts_by_round or {}
    affect_by_agent = affect_by_agent or {}

    cap = int(_cfg("DECISION_CHANNEL_MAX_ACTIVE", max_active_per_round) or max_active_per_round)
    if concurrency is None:
        concurrency = int(_cfg("OASIS_SEMAPHORE", 8) or 8)

    # Group the frozen action log into per-round rosters (one entry per distinct actor),
    # enriching each entry with its meta + this round's post + affect (R2-SIM-1).
    by_round: Dict[int, "Dict[Any, Dict[str, Any]]"] = {}
    for a in (actions or []):
        if not isinstance(a, dict):
            continue
        try:
            rnd = int(a.get("round", a.get("round_num", 0)) or 0)
        except (TypeError, ValueError):
            continue
        aid = a.get("agent_id")
        if aid is None or rnd < 1:
            continue
        roster = by_round.setdefault(rnd, {})
        if aid not in roster:
            base = meta.get(aid, {"agent_id": aid, "name": a.get("agent_name", ""),
                                  "stance": "", "influence": ""})
            entry = dict(base)
            post = (posts_by_round.get(rnd, {}) or {}).get(aid) or a.get("post") or a.get("text")
            if post:
                entry["post"] = str(post)
            if affect_by_agent.get(aid):
                entry["affect"] = str(affect_by_agent[aid])
            roster[aid] = entry

    ordered_rounds = sorted(by_round)
    rosters: Dict[int, List[Dict[str, Any]]] = {
        rnd: _build_active_roster(list(by_round[rnd].values()), activation, power, cap)
        for rnd in ordered_rounds
    }
    as_of_by_round: Dict[int, Optional[str]] = {
        rnd: _safe_round_to_date(round_to_date, rnd) for rnd in ordered_rounds
    }

    # Phase 1 (parallel): one elicitation per unique (roster, date) key (R2-EXEC-10 cache
    # + SIM-6/R2-EXEC-8 fan-out). round_num for the prompt is the first round using the key.
    tasks: Dict[Any, Tuple[List[Dict[str, Any]], int, Optional[str]]] = {}
    round_key: Dict[int, Any] = {}
    for rnd in ordered_rounds:
        active = rosters[rnd]
        key = (_roster_signature(active), as_of_by_round[rnd])
        round_key[rnd] = key
        if key not in tasks:
            tasks[key] = (active, rnd, as_of_by_round[rnd])
    results = _fan_out_elicit(llm, scenarios, tasks, base_shares, abstain_allowed, concurrency)

    # Phase 2 (serial replay, strict round order): step the single shared WorldState.
    avg_gap = _avg_gap_days(seed, as_of_by_round)
    conv_window = max(1, int(_cfg("SIM_CONVERGENCE_WINDOW", 3) or 3))
    trajectory: List[Dict[str, Any]] = [{"round": 0, **ws.outcome()}]
    all_decisions: List[Dict[str, Any]] = []
    converged_at: Optional[int] = None
    stable_streak = 0
    prev_date = seed.get("as_of_date")
    for rnd in ordered_rounds:
        active = rosters[rnd]
        pmap = {e.get("agent_id"): e.get("outcome_power", 1.0) for e in active}
        decisions = [dict(d) for d in results.get(round_key[rnd], [])]
        for d in decisions:
            d["round"] = rnd
            if d.get("agent_id") in pmap:  # R2-SIM-2: carry the actor's outcome power
                d["outcome_power"] = pmap[d["agent_id"]]
        all_decisions.extend(decisions)
        as_of = as_of_by_round[rnd]
        eff_inertia = _inertia_for_gap(inertia, prev_date, as_of, avg_gap)  # R2-SIM-12
        ws.step(commitments_from_decisions(decisions), inertia=eff_inertia)
        snap = {"round": rnd, **ws.outcome()}
        if as_of:
            snap["as_of"] = as_of
        trajectory.append(snap)
        # SIM-1: windowed convergence — require the EWMA delta to stay below eps for
        # SIM_CONVERGENCE_WINDOW consecutive rounds (after a 2-round warmup) before the
        # trajectory is declared settled. Recorded as a calibration signal regardless of
        # whether the caller chooses to early-stop the live sim on it.
        if ws.converged(conv_eps):
            stable_streak += 1
            if converged_at is None and stable_streak >= conv_window and rnd >= 2:
                converged_at = rnd
        else:
            stable_streak = 0
        if as_of:
            prev_date = as_of

    ws.converged_at = converged_at
    out = ws.outcome()
    out["converged_at"] = converged_at
    return {
        "outcome": out,
        "trajectory": trajectory,
        "decisions": all_decisions,
        "converged_at": converged_at,
        "n_rounds": max(ordered_rounds, default=0),
        "scenarios": scenarios,
        "schema_version": 2,
    }
