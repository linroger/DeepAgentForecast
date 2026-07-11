#!/usr/bin/env python3
"""forecast-visuals 捆绑渲染器 —— 管线工件 → 自包含 plotly 图表（WAVE9-RQ4）。

从一个运行目录（--dir，默认 CWD）读取结构化工件并渲染研究图表：

    actors.json        {actors:[{name,influence,salience,role_class,...}],
                        relationships:[{source,target,type,sign/valence/polarity,...}]}
    timeline.json      [{date, event}]
    quantitative.json  [{metric, value, unit, as_of_date, tier, ...}]
    prediction_markets.json
                        {as_of, markets:[{question,implied_yes_prob,volume,...}]}
    sources.json       [{title,url,tier,date,staleness_days,...}]
    charts_data.json   （可选兜底：{actors, relationships, timeline, quantitative,
                        prediction_markets, sources} ——
                        供抽取尚未落盘时由 agent 自写数据的 write-step 流程）

输出（全部落在 <dir>/charts/ 下 + <dir>/charts.json 清单）：
    charts/actor_network.{html,png}   actor 关系网络（角色类着色、影响力定径、valence 定边色）
    charts/timeline.{html,png}        分道事件时间线（按日期，≤40 事件，悬停出全文）
    charts/quant_metrics.{html,png}   定量 Top 指标（同单位分组取最大组，横向条形）
    charts/market_probabilities.{html,png}
                                      最高流动性预测市场的隐含 P(yes)
    charts/source_quality.{html,png}  来源层级构成 + 时效性分布

设计约束（与 SKILL.md 的失败模式清单一致）：
  * 缺哪个工件跳哪张图，绝不造数据；单图失败绝不拖垮其余（每图独立 try/except）。
  * HTML 自包含（plotly.js 内联），无网络依赖。
  * PNG：kaleido 可导入且能出图 → 用之；否则 matplotlib 简化重绘；两者皆无 → 只出
    HTML（清单条目仍登记，path 指向 html）。
  * plotly 缺失 → matplotlib-only PNG；plotly 与 matplotlib 都缺 → exit 3 并给出明确
    stderr（调用方按 degrade-safe 跳过整步）。
  * charts.json 原子整写（读旧清单 → 按稳定生产者 ID 替换 → 重写），幂等可重跑。

Exit codes: 0 = 至少写出一张图或清单已更新；2 = 无任何可渲染工件；3 = 无图形库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# 图形库探测（degrade 阶梯：plotly(+kaleido) → matplotlib → 无库退出 3）
# ---------------------------------------------------------------------------
try:
    import plotly.graph_objects as go  # type: ignore
    from plotly.subplots import make_subplots  # type: ignore
    _HAS_PLOTLY = True
except Exception:  # noqa: BLE001 — 导入失败即降级
    go = None  # type: ignore
    make_subplots = None  # type: ignore
    _HAS_PLOTLY = False

try:
    import matplotlib
    matplotlib.use("Agg")  # 无头环境
    import matplotlib.pyplot as plt  # type: ignore
    _HAS_MPL = True
except Exception:  # noqa: BLE001
    plt = None  # type: ignore
    _HAS_MPL = False

# 角色类 → 颜色（与 SKILL.md 的 role_class 分组一致；未知类回退灰）。
_ROLE_COLORS = {
    "principal": "#3b6fb0",
    "arbiter": "#8250df",
    "stakeholder": "#2f8f5b",
    "amplifier": "#c0603a",
    "intermediary": "#b08a2e",
}
_ROLE_FALLBACK = "#6e7781"
# 边 valence → 颜色：对抗红 / 合作绿 / 治理紫 / 其他灰。
_EDGE_COLORS = {
    "adversarial": "#c04a3a",
    "cooperative": "#2f8f5b",
    "governance": "#8250df",
}
_EDGE_FALLBACK = "#9aa0a6"

# 本渲染器独占的五个生产者。charts.json 还可以包含 agent/用户自定义图表，
# 所以清理时只能替换这些稳定身份，不能整个覆盖 manifest。
_OWNED_CHART_IDS = (
    "actor_network",
    "timeline",
    "quant_metrics",
    "market_probabilities",
    "source_quality",
)
def _log(msg: str) -> None:
    print(f"[render] {msg}", file=sys.stderr)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _num(v) -> float | None:
    """从 value 抽首个数字（'54' / '54.2' / '~$54B（2026 capex）' 都取 54/54.2）。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if v == v else None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _truncate(s: str, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _parse_date(v) -> dt.date | None:
    """Parse an ISO-like source date without consulting wall-clock time."""
    raw = str(v or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _source_identity(row: dict) -> str:
    """Canonicalize a source identity without lowercasing case-sensitive paths."""
    raw_url = str(row.get("url") or "").strip()
    if raw_url:
        try:
            parsed = urlsplit(raw_url)
            if parsed.scheme and parsed.netloc:
                path = parsed.path.rstrip("/")
                return urlunsplit(
                    (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
                )
        except ValueError:
            pass
        return raw_url.rstrip("/")
    return str(row.get("title") or "").strip().lower()


# ---------------------------------------------------------------------------
# 数据准备（纯函数——plotly 与 matplotlib 两个渲染面消费同一份 prep）
# ---------------------------------------------------------------------------


def prep_network(actors_obj, max_nodes: int = 30):
    """actors.json → 节点（按 role_class 分组圆布局、影响力定径）+ 边（valence 定色）。"""
    if not isinstance(actors_obj, dict):
        return None
    actors = [a for a in (actors_obj.get("actors") or []) if isinstance(a, dict) and str(a.get("name") or "").strip()]
    if not actors:
        return None

    def _influence(a) -> float:
        for k in ("influence", "salience", "salience_score"):
            v = _num(a.get(k))
            if v is not None:
                return v
        return 0.0

    actors = sorted(actors, key=lambda a: -_influence(a))[: max(2, max_nodes)]
    names = [str(a["name"]).strip() for a in actors]
    name_set = {n.lower() for n in names}
    # 角色类分组圆布局：同 role_class 的节点相邻（角度连续段），确定性（无随机布局）。
    groups: dict[str, list[int]] = {}
    for i, a in enumerate(actors):
        groups.setdefault(str(a.get("role_class") or "other").strip().lower() or "other", []).append(i)
    pos: dict[int, tuple[float, float]] = {}
    ordered = [i for g in sorted(groups) for i in groups[g]]
    for rank, i in enumerate(ordered):
        theta = 2.0 * math.pi * rank / max(1, len(ordered))
        pos[i] = (math.cos(theta), math.sin(theta))
    infl = [_influence(a) for a in actors]
    max_i = max(infl) or 1.0
    nodes = []
    for i, a in enumerate(actors):
        rc = str(a.get("role_class") or "other").strip().lower() or "other"
        nodes.append({
            "name": names[i],
            "x": pos[i][0], "y": pos[i][1],
            "size": 14 + 26 * (infl[i] / max_i),
            "color": _ROLE_COLORS.get(rc, _ROLE_FALLBACK),
            "role_class": rc,
        })
    idx = {n.lower(): i for i, n in enumerate(names)}
    edges = []
    seen_edges: set = set()
    for r in actors_obj.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        s = str(r.get("source") or "").strip()
        t = str(r.get("target") or "").strip()
        if s.lower() not in name_set or t.lower() not in name_set or s.lower() == t.lower():
            continue
        rtype = str(r.get("type") or "").strip().upper()
        key = (s.lower(), t.lower(), rtype)
        if key in seen_edges:  # 重复边去重（诊断里的 COMPETES_WITH ×4）
            continue
        seen_edges.add(key)
        valence = str(r.get("valence") or r.get("sign") or r.get("polarity") or "").strip().lower()
        i, j = idx[s.lower()], idx[t.lower()]
        edges.append({
            "x0": pos[i][0], "y0": pos[i][1], "x1": pos[j][0], "y1": pos[j][1],
            "color": _EDGE_COLORS.get(valence, _EDGE_FALLBACK),
            "label": f"{s} —{rtype or 'REL'}→ {t}" + (f" ({valence})" if valence else ""),
        })
    if not edges and len(nodes) < 3:
        return None
    return {"nodes": nodes, "edges": edges}


def prep_timeline(rows, max_events: int = 40):
    """timeline.json → 按日期排序、近端优先截断、轮转分道（防标签互压）。"""
    events = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        date = str(r.get("date") or "").strip()
        event = str(r.get("event") or "").strip()
        if date and event:
            events.append((date, event))
    if not events:
        return None
    # 去重（同日期同文案）后按日期排序；超上限保最近的 max_events 条。
    events = sorted(set(events), key=lambda e: e[0])
    if len(events) > max_events:
        events = events[-max_events:]
    points = [{"date": d, "lane": (i % 4) + 1, "label": _truncate(ev, 90), "full": ev}
              for i, (d, ev) in enumerate(events)]
    return {"points": points}


def prep_quant(rows, max_bars: int = 12):
    """quantitative.json → 最大同单位组的 Top 指标横向条形（绝不混单位同轴）。"""
    by_unit: dict[str, list[dict]] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        v = _num(r.get("value"))
        metric = str(r.get("metric") or "").strip()
        unit = str(r.get("unit") or "").strip()
        if v is None or not metric or not unit:
            continue
        by_unit.setdefault(unit, []).append({
            "metric": metric, "value": v,
            "as_of": str(r.get("as_of_date") or "").strip(),
            "tier": str(r.get("tier") or "").strip(),
        })
    if not by_unit:
        return None
    unit = max(by_unit, key=lambda u: len(by_unit[u]))
    rows_u = sorted(by_unit[unit], key=lambda r: -abs(r["value"]))[:max_bars]
    if not rows_u:
        return None
    rows_u.reverse()  # 横向条形自下而上升序展示
    bars = [{"label": _truncate(f"{r['metric']}" + (f" (as of {r['as_of']})" if r["as_of"] else ""), 70),
             "value": r["value"]} for r in rows_u]
    return {"unit": unit, "bars": bars, "n_units": len(by_unit)}


def prep_markets(payload, max_bars: int = 12):
    """prediction_markets.json → most-liquid matched markets with P(yes)."""
    as_of = ""
    if isinstance(payload, dict):
        rows = payload.get("markets") or []
        as_of = str(payload.get("as_of") or "").strip()
    elif isinstance(payload, list):
        rows = payload
    else:
        return None

    by_identity: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        question = str(row.get("question") or row.get("title") or "").strip()
        probability = _num(
            row.get("implied_yes_prob", row.get("yes_probability", row.get("probability")))
        )
        if probability is not None and 1.0 < probability <= 100.0:
            probability /= 100.0
        if not question or probability is None or not (0.0 < probability < 1.0):
            continue
        volume = max(0.0, _num(row.get("volume")) or 0.0)
        identity = str(row.get("market_id") or question).strip().lower()
        candidate = {
            "label": _truncate(question, 76),
            "question": question,
            "probability": probability,
            "volume": volume,
            "liquidity": max(0.0, _num(row.get("liquidity")) or 0.0),
            "market_id": str(row.get("market_id") or "").strip(),
            "end_date": str(row.get("end_date") or "").strip(),
        }
        previous = by_identity.get(identity)
        if previous is None or (candidate["volume"], candidate["question"]) > (
            previous["volume"], previous["question"]
        ):
            by_identity[identity] = candidate
    if not by_identity:
        return None

    markets = sorted(
        by_identity.values(),
        key=lambda row: (-row["volume"], row["question"].lower(), row["market_id"]),
    )[:max_bars]
    markets.reverse()  # horizontal bars: most liquid appears at the top
    return {"markets": markets, "as_of": as_of}


def prep_sources(payload):
    """sources.json → deduplicated source-tier and freshness distributions.

    Explicit ``staleness_days`` wins. Otherwise freshness is measured against
    the latest parseable source date in the artifact, keeping this pure and
    deterministic instead of consulting today's date.
    """
    if isinstance(payload, dict):
        rows = payload.get("sources") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        return None

    unique: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _source_identity(row)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        source_date = _parse_date(
            row.get("date") or row.get("publication_date") or row.get("published_at")
            or row.get("as_of_date")
        )
        unique.append({"row": row, "date": source_date})
    if not unique:
        return None

    dated = [item["date"] for item in unique if item["date"] is not None]
    reference_date = max(dated) if dated else None
    tier_order = ("S1", "S2", "S3", "S4", "Untiered")
    freshness_order = ("≤30 days", "31–90 days", "91–365 days", ">365 days", "Undated")
    tier_counts = dict.fromkeys(tier_order, 0)
    freshness_counts = dict.fromkeys(freshness_order, 0)
    explicit_staleness_count = 0

    for item in unique:
        row = item["row"]
        tier_match = re.search(r"\bS([1-4])\b", str(row.get("tier") or "").upper())
        tier_counts[f"S{tier_match.group(1)}" if tier_match else "Untiered"] += 1

        age = _num(row.get("staleness_days"))
        if age is not None:
            explicit_staleness_count += 1
            age = max(0.0, age)
        if age is None and item["date"] is not None and reference_date is not None:
            age = float(max(0, (reference_date - item["date"]).days))
        if age is None:
            freshness_counts["Undated"] += 1
        elif age <= 30:
            freshness_counts["≤30 days"] += 1
        elif age <= 90:
            freshness_counts["31–90 days"] += 1
        elif age <= 365:
            freshness_counts["91–365 days"] += 1
        else:
            freshness_counts[">365 days"] += 1

    return {
        "tiers": [{"label": label, "count": tier_counts[label]} for label in tier_order],
        "freshness": [
            {"label": label, "count": freshness_counts[label]} for label in freshness_order
        ],
        "total": len(unique),
        "reference_date": reference_date.isoformat() if reference_date else "",
        "explicit_staleness_count": explicit_staleness_count,
    }


# ---------------------------------------------------------------------------
# 渲染（plotly HTML + PNG；PNG 走 kaleido，失败降 matplotlib，再失败只出 HTML）
# ---------------------------------------------------------------------------


def _write_outputs(fig, mpl_draw, charts_dir: Path, stem: str) -> tuple[str | None, str | None]:
    """写 <stem>.html（plotly 可用时）+ <stem>.png（kaleido → matplotlib 降级）。
    返回 (png 相对路径 | None, html 相对路径 | None)；两者皆 None = 该图失败。"""
    html_rel = png_rel = None
    if _HAS_PLOTLY and fig is not None:
        html_path = charts_dir / f"{stem}.html"
        try:
            fig.write_html(
                str(html_path),
                include_plotlyjs=True,
                full_html=True,
                div_id=f"forecast-visual-{stem}",
            )
            html_rel = f"charts/{stem}.html"
        except Exception as e:  # noqa: BLE001
            _log(f"{stem}: write_html failed ({type(e).__name__}: {e})")
        png_path = charts_dir / f"{stem}.png"
        try:
            fig.write_image(str(png_path), width=1200, height=750, scale=2)  # 需要 kaleido
            png_rel = f"charts/{stem}.png"
        except Exception as e:  # noqa: BLE001 — kaleido 缺失/Chrome 不可用 → matplotlib 降级
            _log(f"{stem}: plotly PNG export unavailable ({type(e).__name__}); trying matplotlib fallback")
    if png_rel is None and _HAS_MPL and mpl_draw is not None:
        png_path = charts_dir / f"{stem}.png"
        try:
            mpl_draw(str(png_path))
            png_rel = f"charts/{stem}.png"
        except Exception as e:  # noqa: BLE001
            _log(f"{stem}: matplotlib fallback failed ({type(e).__name__}: {e})")
    return png_rel, html_rel


def render_network(prep, charts_dir: Path):
    fig = None
    if _HAS_PLOTLY:
        fig = go.Figure()
        for e in prep["edges"]:
            fig.add_trace(go.Scatter(x=[e["x0"], e["x1"]], y=[e["y0"], e["y1"]], mode="lines",
                                     line={"color": e["color"], "width": 1.2},
                                     hoverinfo="text", text=e["label"], showlegend=False))
        nodes = prep["nodes"]
        fig.add_trace(go.Scatter(
            x=[n["x"] for n in nodes], y=[n["y"] for n in nodes],
            mode="markers+text", text=[n["name"] for n in nodes], textposition="top center",
            textfont={"size": 10},
            marker={"size": [n["size"] for n in nodes], "color": [n["color"] for n in nodes],
                        "line": {"color": "#ffffff", "width": 1}},
            hovertext=[f"{n['name']} ({n['role_class']})" for n in nodes], hoverinfo="text",
            showlegend=False))
        fig.update_layout(title="Actor Relationship Network", template="plotly_white",
                          xaxis={"visible": False}, yaxis={"visible": False},
                          margin={"l": 20, "r": 20, "t": 60, "b": 20})

    def mpl_draw(path: str) -> None:
        f, ax = plt.subplots(figsize=(12, 7.5), dpi=160)
        for e in prep["edges"]:
            ax.plot([e["x0"], e["x1"]], [e["y0"], e["y1"]], color=e["color"], lw=0.9, alpha=0.7, zorder=1)
        for n in prep["nodes"]:
            ax.scatter([n["x"]], [n["y"]], s=n["size"] ** 1.6, c=n["color"], zorder=2, edgecolors="white")
            ax.annotate(n["name"], (n["x"], n["y"]), textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=7)
        ax.set_title("Actor Relationship Network")
        ax.axis("off")
        f.tight_layout()
        f.savefig(path)
        plt.close(f)

    return _write_outputs(fig, mpl_draw, charts_dir, "actor_network")


def render_timeline(prep, charts_dir: Path):
    pts = prep["points"]
    fig = None
    if _HAS_PLOTLY:
        fig = go.Figure(go.Scatter(
            x=[p["date"] for p in pts], y=[p["lane"] for p in pts],
            mode="markers+text", text=[_truncate(p["label"], 40) for p in pts],
            textposition="top center", textfont={"size": 8},
            hovertext=[f"{p['date']}: {p['full']}" for p in pts], hoverinfo="text",
            marker={"size": 9, "color": "#3b6fb0"}))
        fig.update_layout(title=f"Event Timeline ({len(pts)} key events)", template="plotly_white",
                          yaxis={"visible": False, "range": [0, 5.5]}, xaxis_title="date",
                          margin={"l": 30, "r": 30, "t": 60, "b": 40})

    def mpl_draw(path: str) -> None:
        f, ax = plt.subplots(figsize=(12, 7.5), dpi=160)
        xs = list(range(len(pts)))
        ax.scatter(xs, [p["lane"] for p in pts], s=28, c="#3b6fb0", zorder=2)
        for x, p in zip(xs, pts, strict=False):
            ax.annotate(f"{p['date']}\n{_truncate(p['label'], 34)}", (x, p["lane"]),
                        textcoords="offset points", xytext=(0, 8), ha="center", fontsize=6)
        ax.set_title(f"Event Timeline ({len(pts)} key events)")
        ax.set_ylim(0, 5.5)
        ax.set_yticks([])
        ax.set_xticks([])
        f.tight_layout()
        f.savefig(path)
        plt.close(f)

    return _write_outputs(fig, mpl_draw, charts_dir, "timeline")


def render_quant(prep, charts_dir: Path):
    bars = prep["bars"]
    title = f"Top Quantitative Metrics ({prep['unit']})"
    fig = None
    if _HAS_PLOTLY:
        fig = go.Figure(go.Bar(
            x=[b["value"] for b in bars], y=[b["label"] for b in bars], orientation="h",
            marker={"color": "#2f8f5b"}))
        fig.update_layout(title=title, template="plotly_white", xaxis_title=prep["unit"],
                          margin={"l": 280, "r": 40, "t": 60, "b": 40})

    def mpl_draw(path: str) -> None:
        f, ax = plt.subplots(figsize=(12, 7.5), dpi=160)
        ax.barh([b["label"] for b in bars], [b["value"] for b in bars], color="#2f8f5b")
        ax.set_title(title)
        ax.set_xlabel(prep["unit"])
        ax.tick_params(axis="y", labelsize=7)
        f.tight_layout()
        f.savefig(path)
        plt.close(f)

    return _write_outputs(fig, mpl_draw, charts_dir, "quant_metrics")


def render_markets(prep, charts_dir: Path):
    markets = prep["markets"]
    probabilities = [row["probability"] * 100.0 for row in markets]
    as_of = prep.get("as_of") or ""
    title = "Prediction-Market Implied Probabilities"
    if as_of:
        parsed_as_of = _parse_date(as_of)
        title += f" (as of {parsed_as_of.isoformat() if parsed_as_of else _truncate(as_of, 24)})"
    fig = None
    if _HAS_PLOTLY:
        customdata = [
            [row["volume"], row["liquidity"], row["market_id"], row["end_date"]]
            for row in markets
        ]
        fig = go.Figure(go.Bar(
            x=probabilities,
            y=[row["label"] for row in markets],
            orientation="h",
            marker={
                "color": probabilities,
                "colorscale": [[0.0, "#dce8f5"], [0.5, "#6f9fca"], [1.0, "#245b8f"]],
                "cmin": 0,
                "cmax": 100,
            },
            text=[f"{probability:.1f}%" for probability in probabilities],
            textposition="auto",
            customdata=customdata,
            hovertemplate=(
                "%{y}<br>P(yes): %{x:.1f}%<br>Volume: $%{customdata[0]:,.0f}"
                "<br>Liquidity: $%{customdata[1]:,.0f}<br>Market: %{customdata[2]}"
                "<br>Ends: %{customdata[3]}<extra></extra>"
            ),
        ))
        fig.add_vline(x=50, line_dash="dot", line_color="#6e7781", line_width=1)
        fig.update_layout(
            title=title,
            template="plotly_white",
            xaxis={"title": "market-implied P(yes)", "range": [0, 100], "ticksuffix": "%"},
            margin={"l": 360, "r": 40, "t": 70, "b": 40},
            showlegend=False,
        )

    def mpl_draw(path: str) -> None:
        f, ax = plt.subplots(figsize=(12, 7.5), dpi=160)
        bars = ax.barh([row["label"] for row in markets], probabilities, color="#3b6fb0")
        if hasattr(ax, "bar_label"):
            ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=7)
        ax.axvline(50, color="#6e7781", lw=1, ls="--")
        ax.set_xlim(0, 100)
        ax.set_title(title)
        ax.set_xlabel("market-implied P(yes), %")
        ax.tick_params(axis="y", labelsize=7)
        f.tight_layout()
        f.savefig(path)
        plt.close(f)

    return _write_outputs(fig, mpl_draw, charts_dir, "market_probabilities")


def render_sources(prep, charts_dir: Path):
    # barh draws the final row at the top; reverse so S1/freshest lead visually.
    tiers = list(reversed(prep["tiers"]))
    freshness = list(reversed(prep["freshness"]))
    reference = prep.get("reference_date") or ""
    title = f"Source Quality and Freshness ({prep['total']} unique sources)"
    fig = None
    if _HAS_PLOTLY:
        fig = make_subplots(
            rows=1,
            cols=2,
            subplot_titles=("Evidence tier", "Freshness"),
            horizontal_spacing=0.18,
        )
        fig.add_trace(
            go.Bar(
                x=[row["count"] for row in tiers],
                y=[row["label"] for row in tiers],
                orientation="h",
                marker_color="#8250df",
                hovertemplate="%{y}: %{x} sources<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Bar(
                x=[row["count"] for row in freshness],
                y=[row["label"] for row in freshness],
                orientation="h",
                marker_color="#2f8f5b",
                hovertemplate="%{y}: %{x} sources<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=2,
        )
        if reference and prep.get("explicit_staleness_count"):
            freshness_note = (
                "Explicit staleness_days are preserved; other dated rows are relative to "
                f"{reference}."
            )
        elif reference:
            freshness_note = f"Dated buckets are relative to {reference}; undated rows are explicit."
        else:
            freshness_note = "No parseable source dates; freshness is reported as undated."
        fig.update_layout(
            title={"text": f"{title}<br><sup>{freshness_note}</sup>"},
            template="plotly_white",
            margin={"l": 100, "r": 40, "t": 100, "b": 45},
        )
        fig.update_xaxes(title_text="source count", row=1, col=1)
        fig.update_xaxes(title_text="source count", row=1, col=2)

    def mpl_draw(path: str) -> None:
        f, axes = plt.subplots(1, 2, figsize=(12, 7.5), dpi=160)
        axes[0].barh([row["label"] for row in tiers], [row["count"] for row in tiers],
                     color="#8250df")
        axes[0].set_title("Evidence tier")
        axes[0].set_xlabel("source count")
        axes[1].barh(
            [row["label"] for row in freshness],
            [row["count"] for row in freshness],
            color="#2f8f5b",
        )
        axes[1].set_title("Freshness" + (f" vs {reference}" if reference else ""))
        axes[1].set_xlabel("source count")
        f.suptitle(title)
        f.tight_layout()
        f.savefig(path)
        plt.close(f)

    return _write_outputs(fig, mpl_draw, charts_dir, "source_quality")


def _owned_chart_id(row: dict) -> str | None:
    """返回条目所属的本渲染器生产者 ID。

    新清单以显式 ``id`` 为权威身份；旧清单没有 id，才从规范输出文件
    stem 恢复身份。``source_data`` 不能证明所有权，因为自定义生产者可以
    合法消费同一个输入。显式声明其它 id 的条目始终视为外部所有。
    """
    explicit = row.get("id")
    if isinstance(explicit, str) and explicit.strip():
        normalized = explicit.strip().lower()
        return normalized if normalized in _OWNED_CHART_IDS else None

    # Legacy renderer rows predate explicit IDs, but their canonical output
    # stem is stable. Infer ownership from that stem only: ``source_data`` is
    # shared by custom producers and is therefore insufficient evidence.
    for field in ("path", "html_path", "png_path"):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        stem = Path(value.strip().replace("\\", "/")).stem.lower()
        if stem in _OWNED_CHART_IDS:
            return stem
    return None


def _merge_manifest(manifest_path: Path, new_entries: list) -> list:
    """替换本渲染器的条目，保留其它生产者的条目。

    一次调用总是检查 actor/timeline/quant/market/source 五个当前输入，因此旧的五类
    条目必须先作为一个集合删除，再按固定顺序加入本次成功产物。
    这同时解决 HTML-only → PNG+HTML 重跑时 ``path`` 改变导致的双条目，
    以及当前输入已缺失时清单仍指向旧图的问题。
    """
    existing = _read_json(manifest_path)
    rows = [r for r in existing if isinstance(r, dict)] if isinstance(existing, list) else []

    # 不属于本渲染器的条目（包括无法识别的旧/损坏 dict）按原顺序保留。
    merged = [row for row in rows if _owned_chart_id(row) is None]

    # 每个稳定生产者最多一条；即使调用方误传重复项，也由后者确定性覆盖。
    current: dict[str, dict] = {}
    for entry in new_entries:
        if not isinstance(entry, dict):
            continue
        owned_id = _owned_chart_id(entry)
        if owned_id:
            current[owned_id] = entry

    merged.extend(current[owned_id] for owned_id in _OWNED_CHART_IDS if owned_id in current)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="forecast-visuals bundled renderer (plotly-local)")
    ap.add_argument(
        "--dir",
        default=".",
        help=(
            "run working dir holding actors.json/timeline.json/quantitative.json/"
            "prediction_markets.json/sources.json"
        ),
    )
    args = ap.parse_args()
    if not _HAS_PLOTLY and not _HAS_MPL:
        _log(
            "neither plotly nor matplotlib importable — install one "
            "(pip install plotly kaleido) or use the skill's deterministic table fallback"
        )
        return 3

    base = Path(args.dir).resolve()
    charts_dir = base / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # 工件加载：一等公民文件优先，charts_data.json 兜底（write-step 由 agent 自写数据的流程）。
    fallback = _read_json(base / "charts_data.json") or {}
    actors_obj = _read_json(base / "actors.json")
    if not isinstance(actors_obj, dict):
        actors_obj = {"actors": fallback.get("actors"), "relationships": fallback.get("relationships")} \
            if isinstance(fallback, dict) and fallback.get("actors") else None
    timeline_rows = _read_json(base / "timeline.json")
    if not isinstance(timeline_rows, list):
        timeline_rows = fallback.get("timeline") if isinstance(fallback, dict) else None
    quant_rows = _read_json(base / "quantitative.json")
    if not isinstance(quant_rows, list):
        quant_rows = fallback.get("quantitative") if isinstance(fallback, dict) else None
    markets_payload = _read_json(base / "prediction_markets.json")
    if not isinstance(markets_payload, (dict, list)):
        markets_payload = fallback.get("prediction_markets") if isinstance(fallback, dict) else None
    sources_payload = _read_json(base / "sources.json")
    if not isinstance(sources_payload, (dict, list)):
        sources_payload = fallback.get("sources") if isinstance(fallback, dict) else None

    jobs = [
        ("actor_network", prep_network(actors_obj), render_network,
         "actors.json", "Actor Relationship Network",
         "Node size = influence, color = role class; red edges adversarial, green cooperative."),
        ("timeline", prep_timeline(timeline_rows), render_timeline,
         "timeline.json", "Event Timeline",
         "Dated key events driving the forecast, most recent window."),
        ("quant_metrics", prep_quant(quant_rows), render_quant,
         "quantitative.json", "Top Quantitative Metrics",
         "Largest same-unit metric group; units never mixed on one axis."),
        ("market_probabilities", prep_markets(markets_payload), render_markets,
         "prediction_markets.json", "Prediction-Market Implied Probabilities",
         "Most-liquid matched markets; implied probabilities are calibration anchors, not truth."),
        ("source_quality", prep_sources(sources_payload), render_sources,
         "sources.json", "Source Quality and Freshness",
         "Deduplicated source counts by evidence tier and deterministic freshness bucket."),
    ]
    entries = []
    for stem, prep, renderer, source, title, caption in jobs:
        if not prep:
            _log(f"{stem}: input artifact missing/empty — skipped (never fabricated)")
            continue
        try:
            png_rel, html_rel = renderer(prep, charts_dir)
        except Exception as e:  # noqa: BLE001 — 单图失败绝不拖垮其余
            _log(f"{stem}: render failed ({type(e).__name__}: {e})")
            continue
        path = png_rel or html_rel
        if not path:
            continue
        entry = {"id": stem, "title": title, "caption": caption, "source_data": source, "path": path}
        if html_rel and png_rel:
            entry["html_path"] = html_rel
        entries.append(entry)
        _log(f"{stem}: wrote {path}" + (f" (+ {html_rel})" if html_rel and png_rel else ""))

    manifest_path = base / "charts.json"
    merged = _merge_manifest(manifest_path, entries)
    # 若之前有 manifest，即使本次一张也没生成，也必须落盘清理结果；
    # 否则已删除的输入仍会在 UI 中显示上一轮的旧图。全新空目录不创建空清单。
    if entries or manifest_path.exists():
        _atomic_write(manifest_path, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    if not entries:
        _log(
            "no renderable artifacts found (actors.json/timeline.json/quantitative.json/"
            "prediction_markets.json/sources.json all missing or empty)"
        )
        return 2
    _log(f"charts.json updated ({len(entries)} new/refreshed, {len(merged)} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
