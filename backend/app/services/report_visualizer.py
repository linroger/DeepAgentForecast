"""确定性报告可视化器（VIZ-1）—— 无 LLM、纯数据驱动，把已落盘的结构化工件
（forecast.json / timeline.json / actors.json / world_state_trajectory.json /
comparison.json / 校准账本统计）渲染成两族可视化：

  (A) Mermaid 代码块（零依赖，纯字符串）——时间线、因果路径、派系聚类、角色关系网络。
  (B) matplotlib PNG（Agg 后端，可选依赖）——情景概率误差棒、模型 vs 市场哑铃图、
      结果世界态堆叠面积、基线-情景分组柱、校准曲线。
  (C) plotly 交互式 HTML（ITEM-16，可选依赖）——(B) 中四类图的可交互等价物：情景误差棒、
      模型 vs 市场哑铃、市场价格历史折线、世界态堆叠面积。每图为自包含 charts/<name>.html
      （plotly.js 内联，完全离线可开），manifest type='html'。plotly 缺失 → 整族静默跳过。

设计约束（与 report_agent 的确定性工具族一致）：
  · 纯函数式的 render_mermaid_* 助手：入参是普通 dict/list，返回 markdown 字符串；
    任何畸形输入 → ''（绝不抛异常）。
  · matplotlib 缺失 → 模块级 MATPLOTLIB_AVAILABLE=False，自动进入「仅 Mermaid」模式，
    PNG 构建器全部跳过（degrade-safe）。
  · build_all 汇总出 viz_manifest（[{path,type,source,caption,placement_hint}]）并落盘
    reports/{id}/viz_manifest.json，供报告装配钩子消费。
  · 所有输出确定性：相同工件 → 逐字节相同的 Mermaid / 同样的图表数据。中文+英文双语注释，
    图表标签统一英文（保证 PDF 宽度下可读、避免字体缺失方块）。

env 旋钮（Config，全部 degrade-safe 默认）：
  REPORT_VISUALIZER      主开关（默认开）
  REPORT_VIZ_MERMAID     Mermaid 族开关（默认开）
  REPORT_VIZ_CHARTS      matplotlib PNG 族开关（默认开；matplotlib 缺失时自动无效）
  REPORT_VIZ_DPI         PNG dpi（默认 160）
  REPORT_VIZ_MAX_NODES   网络图/因果图节点上限（默认 40，防止巨图不可读；PM-6 也用作锚点上限）
  REPORT_VIZ_PRICE_HISTORY  PM-6 市场价格历史折线族开关（默认开；Config 缺该键时回退读 os.environ）
  REPORT_VIZ_INTERACTIVE ITEM-16 交互式 plotly HTML 图表族开关（默认开；plotly 缺失时自动无效）
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# matplotlib 可选依赖：导入失败 → 仅 Mermaid 模式（模块级单一真源标志）。
# 必须在任何 pyplot 导入之前强制 Agg 后端（无显示环境/服务器渲染 PNG）。
# ─────────────────────────────────────────────────────────────────────────────
try:  # pragma: no cover - 导入分支由 monkeypatch 测试覆盖
    import matplotlib
    matplotlib.use("Agg")  # 无头后端；必须在 pyplot 之前设置
    import matplotlib.pyplot as plt  # noqa: E402
    MATPLOTLIB_AVAILABLE = True
except Exception:  # noqa: BLE001 - 任何导入/后端失败都降级为仅 Mermaid
    plt = None  # type: ignore
    MATPLOTLIB_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# plotly 可选依赖（ITEM-16 交互式 HTML 图表族）：导入失败 → PLOTLY_AVAILABLE=False，
# HTML 族整体静默跳过（镜像 MATPLOTLIB_AVAILABLE 模式）。与 matplotlib 完全正交，二者可各自
# 缺失/可用而互不影响。plotly 生成的 HTML 在浏览器渲染，CJK 字形由浏览器字体处理（无需字形过滤）。
# ─────────────────────────────────────────────────────────────────────────────
try:  # pragma: no cover - 导入分支由 monkeypatch 测试覆盖
    import plotly.graph_objects as go  # noqa: E402
    PLOTLY_AVAILABLE = True
except Exception:  # noqa: BLE001 - 任何导入失败都降级（不生成 HTML 图族）
    go = None  # type: ignore
    PLOTLY_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# 内部小工具（纯函数）
# ─────────────────────────────────────────────────────────────────────────────
def _cfg(name: str, default: Any) -> Any:
    """从 Config 读旋钮；Config 不可用/缺键 → default（模块可独立于 Flask 运行）。"""
    try:
        from ..config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001
        return default


def _san_label(text: Any, max_len: int = 80) -> str:
    """规整 Mermaid 引号标签内的文本：去掉会破坏语法的字符（双引号/竖线/换行/尖括号），
    折叠空白并截断。空/None → ''。"""
    s = str(text if text is not None else "").strip()
    if not s:
        return ""
    s = s.replace('"', "'").replace("|", "/").replace("\n", " ").replace("\r", " ")
    s = s.replace("<", "(").replace(">", ")").replace("[", "(").replace("]", ")")
    s = s.replace("{", "(").replace("}", ")").replace("#", "＃").replace("`", "'")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


# 已知的中文对比维度名 → 英文（comparison.json 的 dimensions 来自 report_agent 的
# _scenario_diff_structured，维度名为中文；matplotlib 默认字体无 CJK 字形，故映射为英文，
# 保证 PDF 宽度下可读、无缺字方块）。
_COMPARISON_DIM_EN = {
    "总动作量": "Total actions",
    "峰值轮次": "Peak round",
    "执行轮数": "Rounds executed",
    "参与 Agent 数": "Active agents",
    "活跃度变化最大 Agent": "Top mover",
}


def _mpl_text(text: Any, fallback: str = "", max_len: int = 60) -> str:
    """matplotlib 文本标签的字形安全化：先套用已知中文维度→英文映射，再剔除默认字体
    （DejaVu Sans）无法渲染的字形（CJK/假名/谚文等，ord ≥ 0x2E80），折叠空白并截断。
    结果为空 → fallback。避免 PNG 里出现缺字方块 + UserWarning，保证「clean English labels」。"""
    s = str(text if text is not None else "").strip()
    s = _COMPARISON_DIM_EN.get(s, s)
    # 保留 Latin/Greek/Cyrillic/常见标点（< 0x2E80）；丢弃 CJK 及以上的非拉丁字形。
    kept = "".join(ch for ch in s if ord(ch) < 0x2E80)
    kept = re.sub(r"\s+", " ", kept).strip()
    if not kept:
        return fallback
    if len(kept) > max_len:
        kept = kept[: max_len - 1].rstrip() + "…"
    return kept


def _html_text(text: Any, fallback: str = "", max_len: int = 60) -> str:
    """plotly HTML 标签的文本规整（ITEM-16）：先套用已知中文维度→英文映射（与图注一致），但
    保留 CJK/非拉丁字形（浏览器字体可渲染，无缺字方块问题），仅折叠空白并截断。空 → fallback。"""
    s = str(text if text is not None else "").strip()
    s = _COMPARISON_DIM_EN.get(s, s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return fallback
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _node_id(counter: Dict[str, str], name: str) -> str:
    """把任意实体名映射到稳定的 Mermaid 节点 id（n0/n1/…，按首次出现顺序，确定性）。"""
    key = str(name)
    if key not in counter:
        counter[key] = f"n{len(counter)}"
    return counter[key]


def _to_float(v: Any) -> Optional[float]:
    """尽力把值转成 float（支持 '37'、'37%'、'D+3' 里的数字、'round 3 (12)' 抽首数字）。
    失败 → None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            return f if f == f else None  # 过滤 NaN
        except (TypeError, ValueError):
            return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0))
    except (TypeError, ValueError):
        return None


# 关系类型 → 英文边标签（用于因果/网络图；未知类型原样大写）。
_REL_LABEL_EN = {
    "CAUSES": "CAUSES", "ENABLES": "ENABLES", "CONSTRAINS": "CONSTRAINS",
    "OPPOSES": "OPPOSES", "SUPPORTS": "SUPPORTS", "ALLIES": "ALLIES",
    "ALLIED_WITH": "ALLIED", "RIVALS": "RIVALS", "COMPETES": "COMPETES",
    "INFLUENCES": "INFLUENCES", "DEPENDS_ON": "DEPENDS", "PART_OF": "PART_OF",
    "NEGOTIATES": "NEGOTIATES", "FUNDS": "FUNDS", "THREATENS": "THREATENS",
}

# 关系 sign/valence → 符号（用于有向签名边）。
_SIGN_TOKEN = {
    "+": "+", "positive": "+", "supportive": "+", "ally": "+", "allied": "+",
    "-": "−", "negative": "−", "adversarial": "−", "rival": "−", "oppose": "−",
    "0": "±", "neutral": "±", "mixed": "±", "transactional": "±",
}


def _sign_of(rel: Dict[str, Any]) -> str:
    """从关系 dict 推导签名符号（+/−/±）：优先 sign，其次 valence，再看 polarity 数值。"""
    for key in ("sign", "valence", "polarity_label"):
        raw = str(rel.get(key, "") or "").strip().lower()
        if raw in _SIGN_TOKEN:
            return _SIGN_TOKEN[raw]
    pol = _to_float(rel.get("polarity"))
    if pol is not None:
        if pol > 0.15:
            return "+"
        if pol < -0.15:
            return "−"
        return "±"
    return ""


class ReportVisualizer:
    """报告可视化器。所有 render_mermaid_* 为纯静态助手；build_all 为落盘编排入口。

    典型用法（供 report_agent 钩子调用）：
        viz = ReportVisualizer()
        manifest = viz.build_all(report_id, report_dir, artifacts)
    其中 artifacts 是已从 report 文件夹 / handoff 目录读入的普通 dict/list 集合，键名见
    build_all 文档字符串。任何工件缺失/畸形都被静默跳过，绝不阻断报告生成。
    """

    # ============================ (A) Mermaid 族 ============================
    # 全部为 @staticmethod 纯函数：入参普通 dict/list，返回 ```mermaid``` 代码块字符串；
    # 畸形/空输入 → ''（never raises）。

    @staticmethod
    def render_mermaid_timeline(timeline: Any, title: str = "Event Timeline") -> str:
        """timeline.json（[{date,event}] 或 {timeline:[...]}）→ ```mermaid timeline```。

        兼容 zh/en 键：date/日期、event/事件/text/label。按日期升序（无日期项保持原序末尾）。
        无有效条目 → ''。"""
        try:
            rows = timeline.get("timeline") if isinstance(timeline, dict) else timeline
            if not isinstance(rows, list) or not rows:
                return ""
            items: List[Tuple[str, str]] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                date = str(r.get("date") or r.get("日期") or r.get("when") or "").strip()
                event = (r.get("event") or r.get("事件") or r.get("text")
                         or r.get("label") or r.get("description"))
                ev = _san_label(event, max_len=90)
                if not ev:
                    continue
                items.append((date, ev))
            if not items:
                return ""
            # 稳定排序：有日期的按日期，无日期的排到末尾（保持相对顺序）。
            dated = [(d, e) for d, e in items if d]
            undated = [(d, e) for d, e in items if not d]
            dated.sort(key=lambda t: t[0])
            ordered = dated + undated

            lines = ["```mermaid", "timeline", f"    title {_san_label(title, 60)}"]
            # Mermaid timeline 语法：`<period> : <event>`。无日期时用序号占位周期。
            for i, (d, e) in enumerate(ordered, 1):
                period = _san_label(d, 24) if d else f"({i})"
                lines.append(f"    {period} : {e}")
            lines.append("```")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - 纯助手永不抛错
            return ""

    @staticmethod
    def render_mermaid_causal(paths: Any, title: str = "") -> str:
        """因果路径 → ```mermaid flowchart LR```（带签名边标签）。

        接受两种入参（皆来自 KG trace 输出）：
          ① 字符串列表，形如 'A --[CAUSES,+,strong]--> B'（方括号内 逗号分隔：类型,符号,强度）；
          ② 结构化路径列表 [{source,target,relation/type,sign,strength}]（或 {paths:[...]}）。
        边标签为 `TYPE 符号/强度`（例如 `CAUSES +/strong`）。无有效边 → ''。"""
        try:
            if isinstance(paths, dict):
                paths = paths.get("paths") or paths.get("edges") or paths.get("causal") or []
            if not isinstance(paths, list) or not paths:
                return ""
            edges: List[Tuple[str, str, str]] = []  # (source, target, label)
            for p in paths:
                parsed = _parse_causal_item(p)
                if parsed:
                    edges.extend(parsed)
            if not edges:
                return ""
            max_nodes = int(_cfg("REPORT_VIZ_MAX_NODES", 40) or 40)
            counter: Dict[str, str] = {}
            lines = ["```mermaid", "flowchart LR"]
            if title:
                lines.insert(1, f"%% {_san_label(title, 60)}")
            emitted = 0
            for src, tgt, label in edges:
                if len(counter) >= max_nodes and (src not in counter or tgt not in counter):
                    break
                sid = _node_id(counter, src)
                tid = _node_id(counter, tgt)
                lines.append(f'    {sid}["{_san_label(src, 40)}"] '
                             f'-->|"{_san_label(label, 40)}"| {tid}["{_san_label(tgt, 40)}"]')
                emitted += 1
            if emitted == 0:
                return ""
            lines.append("```")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def render_mermaid_coalition(coalition: Any, title: str = "Coalition Map") -> str:
        """派系/联盟聚类 → ```mermaid flowchart TB``` 的 subgraph 簇。

        接受灵活入参：
          ① {clusters:[{label,members:[...]}, ...]} 或 {coalitions:[...]}；
          ② [{label,members:[...]}, ...]；
          ③ [[name,name,...], ...]（每个子列表即一个派系）。
        每个 ≥1 成员的派系渲染为一个 subgraph。无有效派系 → ''。"""
        try:
            clusters = _normalize_clusters(coalition)
            if not clusters:
                return ""
            max_nodes = int(_cfg("REPORT_VIZ_MAX_NODES", 40) or 40)
            lines = ["```mermaid", "flowchart TB", f"%% {_san_label(title, 60)}"]
            mid = 0
            drawn = 0
            for ci, (label, members) in enumerate(clusters, 1):
                clean_members = [_san_label(m, 40) for m in members if _san_label(m, 40)]
                if not clean_members:
                    continue
                lbl = _san_label(label, 50) or f"Faction {ci}"
                lines.append(f'    subgraph c{ci}["{lbl} ({len(clean_members)})"]')
                lines.append("        direction TB")
                for m in clean_members:
                    if mid >= max_nodes:
                        break
                    lines.append(f'        m{mid}["{m}"]')
                    mid += 1
                lines.append("    end")
                drawn += 1
                if mid >= max_nodes:
                    break
            if drawn == 0:
                return ""
            lines.append("```")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def render_mermaid_actor_network(actors: Any, title: str = "Actor Relationship Network") -> str:
        """actors.json 的 relationships[] → ```mermaid graph TD``` 有向类型化网络。

        接受 {relationships:[...]} 或直接 relationships 列表。每条关系需含 source/target；
        边标签为关系类型（英文），签名（+/−/±）追加。仅渲染出现在某条关系里的节点。
        无有效关系 → ''。"""
        try:
            if isinstance(actors, dict):
                rels = actors.get("relationships") or actors.get("relations") or actors.get("edges")
            else:
                rels = actors
            if not isinstance(rels, list) or not rels:
                return ""
            max_nodes = int(_cfg("REPORT_VIZ_MAX_NODES", 40) or 40)
            counter: Dict[str, str] = {}
            lines = ["```mermaid", "graph TD", f"%% {_san_label(title, 60)}"]
            emitted = 0
            for r in rels:
                if not isinstance(r, dict):
                    continue
                src = str(r.get("source") or r.get("from") or r.get("源") or "").strip()
                tgt = str(r.get("target") or r.get("to") or r.get("目标") or "").strip()
                if not src or not tgt:
                    continue
                if len(counter) >= max_nodes and (src not in counter or tgt not in counter):
                    continue
                typ = str(r.get("type") or r.get("relation") or r.get("rel") or "").strip().upper()
                label = _REL_LABEL_EN.get(typ, typ or "REL")
                sign = _sign_of(r)
                if sign:
                    label = f"{label} {sign}"
                sid = _node_id(counter, src)
                tid = _node_id(counter, tgt)
                lines.append(f'    {sid}["{_san_label(src, 40)}"] '
                             f'-->|"{_san_label(label, 30)}"| {tid}["{_san_label(tgt, 40)}"]')
                emitted += 1
            if emitted == 0:
                return ""
            lines.append("```")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    # ============================ (B) matplotlib 族 ============================
    # 全部为实例方法：入参普通 dict/list + 输出目录，成功落盘 PNG 返回相对路径，否则 None。
    # matplotlib 缺失 → 直接 None（build_all 会跳过整族）。一图一 figure，dpi 见旋钮。

    def _chart_ok(self) -> bool:
        return MATPLOTLIB_AVAILABLE and bool(_cfg("REPORT_VIZ_CHARTS", True))

    def _price_hist_ok(self) -> bool:
        """PM-6 市场价格历史折线族开关（默认开）。Config 未定义该属性时回退直接读 os.environ
        （避免跨文件改动 config.py 也能被 env 切换），仍缺 → 默认 True（degrade-safe）。"""
        val = _cfg("REPORT_VIZ_PRICE_HISTORY", None)
        if val is None:
            raw = os.environ.get("REPORT_VIZ_PRICE_HISTORY")
            if raw is None:
                return True
            return raw.strip().lower() == "true"
        return bool(val)

    def _dpi(self) -> int:
        try:
            return max(72, int(_cfg("REPORT_VIZ_DPI", 160) or 160))
        except (TypeError, ValueError):
            return 160

    def _save(self, fig, charts_dir: str, filename: str) -> Optional[str]:
        """把 figure 存成 PNG 并关闭；返回相对 report_dir 的路径 'charts/<file>'。失败 → None。"""
        try:
            os.makedirs(charts_dir, exist_ok=True)
            out_path = os.path.join(charts_dir, filename)
            fig.savefig(out_path, dpi=self._dpi(), bbox_inches="tight")
            return os.path.join("charts", filename)
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                plt.close(fig)
            except Exception:  # noqa: BLE001
                pass

    def build_scenario_bars(self, forecast: Any, charts_dir: str) -> Optional[str]:
        """(1) 情景概率横向柱状 + p_low/p_high 误差棒（来自 forecast.json scenarios）。

        概率键兼容 probability/prob/p；区间键兼容 p_low/p_high、prob_low/prob_high、ci_low/ci_high。
        无区间时不画误差棒（degrade）。无情景 → None。"""
        if not self._chart_ok():
            return None
        try:
            scenarios = forecast.get("scenarios") if isinstance(forecast, dict) else forecast
            if not isinstance(scenarios, list) or not scenarios:
                return None
            names: List[str] = []
            probs: List[float] = []
            lo_err: List[float] = []
            hi_err: List[float] = []
            has_err = False
            for i, s in enumerate(scenarios, 1):
                if not isinstance(s, dict):
                    continue
                p = _to_float(s.get("probability"))
                if p is None:
                    p = _to_float(s.get("prob"))
                if p is None:
                    p = _to_float(s.get("p"))
                if p is None:
                    continue
                nm = _mpl_text(s.get("name") or s.get("label"), fallback=f"Scenario {i}", max_len=48)
                names.append(nm)
                probs.append(p)
                lo = _first_float(s, ("p_low", "prob_low", "ci_low", "low"))
                hi = _first_float(s, ("p_high", "prob_high", "ci_high", "high"))
                if lo is not None and hi is not None and lo <= p <= hi:
                    lo_err.append(max(0.0, p - lo))
                    hi_err.append(max(0.0, hi - p))
                    has_err = True
                else:
                    lo_err.append(0.0)
                    hi_err.append(0.0)
            if not probs:
                return None
            y = list(range(len(names)))[::-1]  # 顶部为第一个情景
            fig, ax = plt.subplots(figsize=(9, max(2.2, 0.7 * len(names) + 1.2)))
            xerr = [lo_err, hi_err] if has_err else None
            ax.barh(y, probs, color="#3b6fb0", height=0.6,
                    xerr=xerr, capsize=4 if has_err else 0,
                    error_kw={"ecolor": "#2b2b2b", "elinewidth": 1.1})
            ax.set_yticks(y)
            ax.set_yticklabels(names, fontsize=9)
            ax.set_xlabel("Probability", fontsize=10)
            ax.set_xlim(0, max(1.0, max(probs) * 1.15))
            ax.set_title("Scenario Probabilities" + (" (with p_low/p_high)" if has_err else ""),
                         fontsize=12, fontweight="bold")
            for yi, p in zip(y, probs):
                ax.text(p + 0.01, yi, f"{p * 100:.0f}%", va="center", fontsize=9)
            ax.grid(axis="x", linestyle=":", alpha=0.4)
            fig.tight_layout()
            return self._save(fig, charts_dir, "scenario_probabilities.png")
        except Exception:  # noqa: BLE001
            return None

    def build_model_vs_market(self, forecast: Any, charts_dir: str) -> Optional[str]:
        """(2) 模型 vs 市场哑铃图（来自 binary_forecasts[].market_anchor 的分歧）。

        每条二元预测：模型概率 vs market_anchor.implied_yes_prob，连线两点。只保留带 market_anchor
        的条目。无可比条目 → None。"""
        if not self._chart_ok():
            return None
        try:
            bfs = forecast.get("binary_forecasts") if isinstance(forecast, dict) else forecast
            if not isinstance(bfs, list) or not bfs:
                return None
            labels: List[str] = []
            model_p: List[float] = []
            market_p: List[float] = []
            for bf in bfs:
                if not isinstance(bf, dict):
                    continue
                anchor = bf.get("market_anchor")
                if not isinstance(anchor, dict):
                    continue
                mp = _to_float(bf.get("probability"))
                kp = _to_float(anchor.get("implied_yes_prob"))
                if kp is None:
                    kp = _to_float(anchor.get("implied_prob"))
                if mp is None or kp is None:
                    continue
                lab = _mpl_text(bf.get("id") or bf.get("statement") or bf.get("market_id")
                                or anchor.get("market_id"), fallback=f"F{len(labels) + 1}", max_len=46)
                labels.append(lab)
                model_p.append(mp)
                market_p.append(kp)
            if not labels:
                return None
            y = list(range(len(labels)))[::-1]
            fig, ax = plt.subplots(figsize=(9, max(2.2, 0.6 * len(labels) + 1.4)))
            for yi, mp, kp in zip(y, model_p, market_p):
                ax.plot([kp, mp], [yi, yi], color="#9aa5b1", linewidth=2, zorder=1)
            ax.scatter(market_p, y, color="#c0603a", s=60, zorder=2, label="Market implied")
            ax.scatter(model_p, y, color="#3b6fb0", s=60, zorder=3, label="Model")
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=9)
            ax.set_xlabel("P(yes)", fontsize=10)
            ax.set_xlim(0, 1)
            ax.set_title("Model vs Market (binary forecasts)", fontsize=12, fontweight="bold")
            ax.grid(axis="x", linestyle=":", alpha=0.4)
            ax.legend(loc="lower right", fontsize=9)
            fig.tight_layout()
            return self._save(fig, charts_dir, "model_vs_market.png")
        except Exception:  # noqa: BLE001
            return None

    def build_worldstate_area(self, trajectory: Any, charts_dir: str) -> Optional[str]:
        """(3) 结果世界态堆叠面积（world_state_trajectory.json 的 trajectory[].shares 随轮次）。

        兼容 {trajectory:[{round,shares:{name:share}}]} 或直接列表；shares 为情景→份额。
        少于 2 个时间点 → None（面积图无意义）。"""
        if not self._chart_ok():
            return None
        try:
            rows = trajectory.get("trajectory") if isinstance(trajectory, dict) else trajectory
            if not isinstance(rows, list) or len(rows) < 2:
                return None
            # 收集所有出现过的情景名（稳定：首次出现顺序）。
            names: List[str] = []
            snaps: List[Tuple[float, Dict[str, float]]] = []
            for i, r in enumerate(rows):
                if not isinstance(r, dict):
                    continue
                shares = r.get("shares")
                if not isinstance(shares, dict) or not shares:
                    continue
                x = _to_float(r.get("round"))
                if x is None:
                    x = float(i)
                clean: Dict[str, float] = {}
                for k, v in shares.items():
                    fv = _to_float(v)
                    if fv is None:
                        continue
                    clean[str(k)] = fv
                    if str(k) not in names:
                        names.append(str(k))
                if clean:
                    snaps.append((x, clean))
            if len(snaps) < 2 or not names:
                return None
            xs = [x for x, _ in snaps]
            series = [[snap.get(nm, 0.0) for _, snap in snaps] for nm in names]
            fig, ax = plt.subplots(figsize=(9, 5))
            leg_labels = [_mpl_text(n, fallback=f"Series {i + 1}", max_len=40)
                          for i, n in enumerate(names)]
            ax.stackplot(xs, *series, labels=leg_labels, alpha=0.85)
            ax.set_xlabel("Simulation round", fontsize=10)
            ax.set_ylabel("Outcome share", fontsize=10)
            ax.set_title("Modeled Outcome-Share Trajectory", fontsize=12, fontweight="bold")
            ax.set_xlim(min(xs), max(xs))
            ax.set_ylim(0, max(1.0, max(sum(col) for col in zip(*series)) if series else 1.0))
            ax.legend(loc="upper left", fontsize=8, ncol=1, framealpha=0.85)
            ax.grid(linestyle=":", alpha=0.35)
            fig.tight_layout()
            return self._save(fig, charts_dir, "worldstate_trajectory.png")
        except Exception:  # noqa: BLE001
            return None

    def build_comparison_bars(self, comparison: Any, charts_dir: str) -> Optional[str]:
        """(4) 基线-情景分组柱（comparison.json 的 dimensions[]，仅取数值可解析的维度）。

        每个维度取 baseline/scenario 两根柱；无法解析为数字的维度跳过。无数值维度 → None。"""
        if not self._chart_ok():
            return None
        try:
            dims = comparison.get("dimensions") if isinstance(comparison, dict) else comparison
            if not isinstance(dims, list) or not dims:
                return None
            labels: List[str] = []
            base_vals: List[float] = []
            scen_vals: List[float] = []
            for d in dims:
                if not isinstance(d, dict):
                    continue
                b = _to_float(d.get("baseline"))
                s = _to_float(d.get("scenario"))
                if b is None or s is None:
                    continue
                labels.append(_mpl_text(d.get("name") or d.get("dimension"),
                                        fallback=f"Dim {len(labels) + 1}", max_len=30))
                base_vals.append(b)
                scen_vals.append(s)
            if not labels:
                return None
            import numpy as _np  # matplotlib 依赖 numpy，一定可用
            x = _np.arange(len(labels))
            w = 0.38
            fig, ax = plt.subplots(figsize=(max(6, 1.4 * len(labels) + 2), 5))
            ax.bar(x - w / 2, base_vals, w, label="Baseline", color="#9aa5b1")
            ax.bar(x + w / 2, scen_vals, w, label="Scenario", color="#3b6fb0")
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=9, rotation=20, ha="right")
            ax.set_ylabel("Value", fontsize=10)
            ax.set_title("Baseline vs Scenario (comparison)", fontsize=12, fontweight="bold")
            ax.legend(fontsize=9)
            ax.grid(axis="y", linestyle=":", alpha=0.4)
            fig.tight_layout()
            return self._save(fig, charts_dir, "comparison_bars.png")
        except Exception:  # noqa: BLE001
            return None

    def build_calibration_curve(self, calibration: Any, charts_dir: str) -> Optional[str]:
        """(5) 校准曲线（来自 forecast-ledger 的 calibration_report 统计，若存在）。

        入参兼容 {bins:[{mean_predicted,observed/hit_rate,...}]} 或直接 bins 列表。
        画 mean_predicted vs observed 折线 + 对角线（完美校准）。有效点 <1 → None。"""
        if not self._chart_ok():
            return None
        try:
            bins = calibration.get("bins") if isinstance(calibration, dict) else calibration
            if not isinstance(bins, list) or not bins:
                return None
            xs: List[float] = []
            ys: List[float] = []
            for b in bins:
                if not isinstance(b, dict):
                    continue
                mp = _first_float(b, ("mean_predicted", "mean_pred", "pred"))
                ob = _first_float(b, ("observed", "hit_rate", "smoothed_hit_rate", "empirical"))
                if mp is None or ob is None:
                    continue
                xs.append(mp)
                ys.append(ob)
            if not xs:
                return None
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            xs = [xs[i] for i in order]
            ys = [ys[i] for i in order]
            fig, ax = plt.subplots(figsize=(6, 6))
            ax.plot([0, 1], [0, 1], color="#9aa5b1", linestyle="--", label="Perfect calibration")
            ax.plot(xs, ys, marker="o", color="#3b6fb0", linewidth=1.8, label="Observed")
            ax.set_xlabel("Mean predicted probability", fontsize=10)
            ax.set_ylabel("Observed frequency", fontsize=10)
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_aspect("equal", adjustable="box")
            ax.set_title("Calibration Curve", fontsize=12, fontweight="bold")
            ax.legend(loc="upper left", fontsize=9)
            ax.grid(linestyle=":", alpha=0.4)
            fig.tight_layout()
            return self._save(fig, charts_dir, "calibration_curve.png")
        except Exception:  # noqa: BLE001
            return None

    def render_market_price_history(self, price_history: Any, anchors: Any,
                                    out_dir: str) -> List[str]:
        """(6) PM-6：每个带市场锚点的二元预测 → market-implied P(yes) 历史价折线图。

        对每个「有市场锚点且 market_id 命中 price_history」的二元预测，画一张：市场隐含
        P(yes) 随时间的折线 + 模型概率作带标签的水平参考线 + 分歧（model − market）标注。
        用途：报告 Part-1「Market Cross-Check」区（placement_hint='binary_forecasts'）。

        入参：
          price_history  {market_id: [{t,p}, ...]}（bridge/report 落盘；t 为 unix 秒，p∈[0,1]）；
          anchors        锚点列表——每项可为二元预测（含 market_anchor 子 dict）或已抽平的
                         {market_id, probability/model_prob, id/statement/label, divergence}。
                         仅保留 market_id 命中 price_history 且有效历史点 ≥2 的锚点，按原序去重。
        分组：锚点数 >6 时每图最多 2 个子图（压缩图数量）；否则一锚一图。锚点总数超
              REPORT_VIZ_MAX_NODES 时确定性截断（保留首现）。
        返回：相对 report_dir 的路径列表 ['charts/market_price_history_1.png', ...]；
        matplotlib 缺失/关闭、无 price_history、无可用锚点 → []（never raises）。"""
        if not self._chart_ok():
            return []
        try:
            if not isinstance(price_history, dict) or not price_history:
                return []
            norm = _normalize_price_anchors(anchors, price_history)
            if not norm:
                return []
            cap = int(_cfg("REPORT_VIZ_MAX_NODES", 40) or 40)
            if cap > 0 and len(norm) > cap:
                norm = norm[:cap]  # 病态超长输入的确定性上限（保留首现锚点）
            import matplotlib.dates as _mdates  # 惰性导入（matplotlib 已确认可用）
            per_fig = 2 if len(norm) > 6 else 1  # >6 锚点 → 每图 2 子图，降低图数量
            groups = [norm[i:i + per_fig] for i in range(0, len(norm), per_fig)]
            paths: List[str] = []
            for gi, group in enumerate(groups, 1):
                n = len(group)
                fig, axes = plt.subplots(n, 1, figsize=(9, max(3.2, 3.0 * n)),
                                         squeeze=False)
                for ai, a in enumerate(group):
                    ax = axes[ai][0]
                    xs = [t for t, _ in a["series"]]
                    ys = [p for _, p in a["series"]]
                    ax.plot(xs, ys, color="#c0603a", linewidth=1.8, marker="o",
                            markersize=3, zorder=2, label="Market implied P(yes)")
                    mp = a["model_p"]
                    if mp is not None:
                        ax.axhline(mp, color="#3b6fb0", linestyle="--", linewidth=1.6,
                                   zorder=1, label=f"Model P(yes) = {mp * 100:.0f}%")
                    div = a["divergence"]
                    if div is not None:
                        ax.annotate(f"Divergence (model − market): {div * 100:+.0f} pp",
                                    xy=(0.02, 0.04), xycoords="axes fraction",
                                    fontsize=8, color="#2b2b2b",
                                    bbox={"boxstyle": "round,pad=0.3", "fc": "#f2f2f2",
                                          "ec": "#9aa5b1", "alpha": 0.85})
                    ax.set_ylim(0, 1)
                    ax.set_ylabel("P(yes)", fontsize=9)
                    ax.set_title(a["label"], fontsize=11, fontweight="bold")
                    ax.xaxis.set_major_formatter(_mdates.DateFormatter("%Y-%m-%d"))
                    ax.legend(loc="best", fontsize=8, framealpha=0.85)
                    ax.grid(linestyle=":", alpha=0.4)
                axes[-1][0].set_xlabel("Date", fontsize=10)
                fig.autofmt_xdate()
                fig.tight_layout()
                rel = self._save(fig, out_dir, f"market_price_history_{gi}.png")
                if rel:
                    paths.append(rel)
            return paths
        except Exception:  # noqa: BLE001
            return []

    # ============================ (C) plotly 交互式 HTML 族 ============================
    # ITEM-16：全部为实例方法，入参普通 dict/list + 输出目录，成功落盘自包含 HTML 返回相对路径，
    # 否则 None（或 []）。plotly 缺失/关闭 → 直接跳过（build_all 会整族略过）。每个 HTML 用
    # include_plotlyjs='inline' 内联 plotly.js，完全离线可开（不依赖 CDN/外链）。数据抽取逻辑与
    # 对应 matplotlib 构建器逐字对齐（复用 _to_float / _first_float / _normalize_price_anchors），
    # 保证同一工件下 PNG 与 HTML 描绘同一份数据。

    def _interactive_ok(self) -> bool:
        """ITEM-16 交互式 HTML 图表族开关（默认开）。plotly 缺失时恒为 False（整族跳过）。"""
        return PLOTLY_AVAILABLE and bool(_cfg("REPORT_VIZ_INTERACTIVE", True))

    def _save_html(self, fig, charts_dir: str, filename: str) -> Optional[str]:
        """把 plotly figure 存成自包含 HTML（plotly.js 内联，离线可开）并返回相对 report_dir 的
        路径 'charts/<file>'。原子写（.tmp→replace），失败 → None。"""
        try:
            os.makedirs(charts_dir, exist_ok=True)
            out_path = os.path.join(charts_dir, filename)
            # include_plotlyjs='inline'：把整份 plotly.js 内联进 HTML → 无外链、完全离线自包含。
            html = fig.to_html(include_plotlyjs="inline", full_html=True,
                               config={"displayModeBar": True, "responsive": True})
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(html)
            os.replace(tmp, out_path)
            return os.path.join("charts", filename)
        except Exception:  # noqa: BLE001 - 落盘失败不阻断其余可视化
            return None

    def build_scenario_bars_html(self, forecast: Any, charts_dir: str) -> Optional[str]:
        """(C1) 情景概率横向柱 + p_low/p_high 误差棒的交互式 HTML（对应 build_scenario_bars）。

        数据抽取与 PNG 版逐字对齐；无区间时不画误差棒。无情景 → None。"""
        if not self._interactive_ok():
            return None
        try:
            scenarios = forecast.get("scenarios") if isinstance(forecast, dict) else forecast
            if not isinstance(scenarios, list) or not scenarios:
                return None
            names: List[str] = []
            probs: List[float] = []
            lo_err: List[float] = []
            hi_err: List[float] = []
            has_err = False
            for i, s in enumerate(scenarios, 1):
                if not isinstance(s, dict):
                    continue
                p = _to_float(s.get("probability"))
                if p is None:
                    p = _to_float(s.get("prob"))
                if p is None:
                    p = _to_float(s.get("p"))
                if p is None:
                    continue
                nm = _html_text(s.get("name") or s.get("label"), fallback=f"Scenario {i}", max_len=48)
                names.append(nm)
                probs.append(p)
                lo = _first_float(s, ("p_low", "prob_low", "ci_low", "low"))
                hi = _first_float(s, ("p_high", "prob_high", "ci_high", "high"))
                if lo is not None and hi is not None and lo <= p <= hi:
                    lo_err.append(max(0.0, p - lo))
                    hi_err.append(max(0.0, hi - p))
                    has_err = True
                else:
                    lo_err.append(0.0)
                    hi_err.append(0.0)
            if not probs:
                return None
            err_x = (dict(type="data", symmetric=False, array=hi_err, arrayminus=lo_err)
                     if has_err else None)
            fig = go.Figure(go.Bar(
                x=probs, y=names, orientation="h", marker_color="#3b6fb0",
                error_x=err_x,
                text=[f"{p * 100:.0f}%" for p in probs], textposition="outside",
                hovertemplate="%{y}: %{x:.1%}<extra></extra>",
            ))
            fig.update_layout(
                title="Scenario Probabilities" + (" (with p_low/p_high)" if has_err else ""),
                xaxis_title="Probability",
                xaxis=dict(range=[0, max(1.0, max(probs) * 1.15)]),
                yaxis=dict(autorange="reversed"),  # 第一个情景显示在顶部（对齐 PNG 版）
                template="plotly_white", margin=dict(l=10, r=10, t=50, b=40),
            )
            return self._save_html(fig, charts_dir, "scenario_probabilities.html")
        except Exception:  # noqa: BLE001
            return None

    def build_model_vs_market_html(self, forecast: Any, charts_dir: str) -> Optional[str]:
        """(C2) 模型 vs 市场哑铃图的交互式 HTML（对应 build_model_vs_market）。

        每条带 market_anchor 的二元预测连模型概率与市场隐含概率两点。无可比条目 → None。"""
        if not self._interactive_ok():
            return None
        try:
            bfs = forecast.get("binary_forecasts") if isinstance(forecast, dict) else forecast
            if not isinstance(bfs, list) or not bfs:
                return None
            labels: List[str] = []
            model_p: List[float] = []
            market_p: List[float] = []
            for bf in bfs:
                if not isinstance(bf, dict):
                    continue
                anchor = bf.get("market_anchor")
                if not isinstance(anchor, dict):
                    continue
                mp = _to_float(bf.get("probability"))
                kp = _to_float(anchor.get("implied_yes_prob"))
                if kp is None:
                    kp = _to_float(anchor.get("implied_prob"))
                if mp is None or kp is None:
                    continue
                lab = _html_text(bf.get("id") or bf.get("statement") or bf.get("market_id")
                                 or anchor.get("market_id"), fallback=f"F{len(labels) + 1}", max_len=46)
                labels.append(lab)
                model_p.append(mp)
                market_p.append(kp)
            if not labels:
                return None
            fig = go.Figure()
            # 先画连线段（哑铃杆），每条一 trace 但不进图例。
            for lab, mp, kp in zip(labels, model_p, market_p):
                fig.add_trace(go.Scatter(
                    x=[kp, mp], y=[lab, lab], mode="lines",
                    line=dict(color="#9aa5b1", width=2),
                    showlegend=False, hoverinfo="skip",
                ))
            fig.add_trace(go.Scatter(
                x=market_p, y=labels, mode="markers", name="Market implied",
                marker=dict(color="#c0603a", size=12),
                hovertemplate="%{y} — market: %{x:.1%}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=model_p, y=labels, mode="markers", name="Model",
                marker=dict(color="#3b6fb0", size=12),
                hovertemplate="%{y} — model: %{x:.1%}<extra></extra>",
            ))
            fig.update_layout(
                title="Model vs Market (binary forecasts)",
                xaxis_title="P(yes)", xaxis=dict(range=[0, 1]),
                yaxis=dict(autorange="reversed"),
                template="plotly_white", margin=dict(l=10, r=10, t=50, b=40),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_html(fig, charts_dir, "model_vs_market.html")
        except Exception:  # noqa: BLE001
            return None

    def build_worldstate_area_html(self, trajectory: Any, charts_dir: str) -> Optional[str]:
        """(C3) 结果世界态堆叠面积的交互式 HTML（对应 build_worldstate_area）。

        兼容 {trajectory:[{round,shares:{name:share}}]} 或直接列表；<2 时间点 → None。"""
        if not self._interactive_ok():
            return None
        try:
            rows = trajectory.get("trajectory") if isinstance(trajectory, dict) else trajectory
            if not isinstance(rows, list) or len(rows) < 2:
                return None
            names: List[str] = []
            snaps: List[Tuple[float, Dict[str, float]]] = []
            for i, r in enumerate(rows):
                if not isinstance(r, dict):
                    continue
                shares = r.get("shares")
                if not isinstance(shares, dict) or not shares:
                    continue
                x = _to_float(r.get("round"))
                if x is None:
                    x = float(i)
                clean: Dict[str, float] = {}
                for k, v in shares.items():
                    fv = _to_float(v)
                    if fv is None:
                        continue
                    clean[str(k)] = fv
                    if str(k) not in names:
                        names.append(str(k))
                if clean:
                    snaps.append((x, clean))
            if len(snaps) < 2 or not names:
                return None
            xs = [x for x, _ in snaps]
            fig = go.Figure()
            for i, nm in enumerate(names):
                ser = [snap.get(nm, 0.0) for _, snap in snaps]
                fig.add_trace(go.Scatter(
                    x=xs, y=ser, mode="lines", name=_html_text(nm, fallback=f"Series {i + 1}", max_len=40),
                    stackgroup="one",  # 堆叠面积
                    hovertemplate="round %{x}: %{y:.2f}<extra>" + _html_text(nm, max_len=40) + "</extra>",
                ))
            fig.update_layout(
                title="Modeled Outcome-Share Trajectory",
                xaxis_title="Simulation round", yaxis_title="Outcome share",
                template="plotly_white", margin=dict(l=10, r=10, t=50, b=40),
            )
            return self._save_html(fig, charts_dir, "worldstate_trajectory.html")
        except Exception:  # noqa: BLE001
            return None

    def render_market_price_history_html(self, price_history: Any, anchors: Any,
                                         out_dir: str) -> List[str]:
        """(C4) 市场价格历史折线的交互式 HTML（对应 render_market_price_history）。

        每个「有市场锚点且 market_id 命中 price_history」的二元预测 → 一张自包含 HTML：市场隐含
        P(yes) 随时间折线 + 模型概率水平参考线 + 分歧标注（交互 hover）。锚点总数超
        REPORT_VIZ_MAX_NODES 确定性截断（保留首现）。返回相对 report_dir 路径列表；
        plotly 缺失/关闭、无 price_history、无可用锚点 → []（never raises）。"""
        if not self._interactive_ok():
            return []
        try:
            if not isinstance(price_history, dict) or not price_history:
                return []
            norm = _normalize_price_anchors(anchors, price_history)
            if not norm:
                return []
            cap = int(_cfg("REPORT_VIZ_MAX_NODES", 40) or 40)
            if cap > 0 and len(norm) > cap:
                norm = norm[:cap]  # 病态超长输入的确定性上限（保留首现锚点）
            paths: List[str] = []
            for gi, a in enumerate(norm, 1):
                xs = [t for t, _ in a["series"]]
                ys = [p for _, p in a["series"]]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines+markers", name="Market implied P(yes)",
                    line=dict(color="#c0603a", width=2), marker=dict(size=5),
                    hovertemplate="%{x|%Y-%m-%d}: %{y:.1%}<extra></extra>",
                ))
                mp = a["model_p"]
                if mp is not None:
                    fig.add_hline(y=mp, line_dash="dash", line_color="#3b6fb0",
                                  annotation_text=f"Model P(yes) = {mp * 100:.0f}%",
                                  annotation_position="top left")
                div = a["divergence"]
                if div is not None:
                    fig.add_annotation(
                        xref="paper", yref="paper", x=0.02, y=0.04, showarrow=False,
                        text=f"Divergence (model − market): {div * 100:+.0f} pp",
                        bgcolor="#f2f2f2", bordercolor="#9aa5b1", borderwidth=1,
                        font=dict(size=11, color="#2b2b2b"),
                    )
                fig.update_layout(
                    title=a["label"],
                    xaxis_title="Date", yaxis_title="P(yes)", yaxis=dict(range=[0, 1]),
                    template="plotly_white", margin=dict(l=10, r=10, t=50, b=40),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                rel = self._save_html(fig, out_dir, f"market_price_history_{gi}.html")
                if rel:
                    paths.append(rel)
            return paths
        except Exception:  # noqa: BLE001
            return []

    # ============================ 编排入口 ============================

    def build_all(self, report_id: str, report_dir: str,
                  artifacts: Dict[str, Any]) -> List[Dict[str, str]]:
        """把所有可用工件渲染成可视化，落盘 charts/*.png + charts/*.mmd（Mermaid 代码块），
        汇总并持久化 viz_manifest.json，返回 manifest 列表。

        artifacts 键（全部可选，缺失即跳过对应可视化）：
          forecast              forecast.json（dict）—— scenarios / binary_forecasts
          timeline              timeline.json（list 或 {timeline:[...]}）
          actors                actors.json（dict，用 relationships[]）
          causal_paths          KG trace 因果路径（str 列表或结构化 dict 列表）
          coalition             派系聚类（结构化，见 render_mermaid_coalition）
          world_state_trajectory  world_state_trajectory.json（dict）
          comparison            comparison.json（dict）
          calibration           校准统计（calibration_report 结果 dict）
          market_price_history  PM-6 市场价格历史 {market_id:[{t,p}]}（可选；缺则回退读
                                reports/{id}/market_price_history.json 与 handoff 下同名文件）
          handoff_dir           PM-6 可选：研究 handoff 目录（用于定位 handoff/market_price_history.json）

        manifest 每项：{path,type,source,caption,placement_hint}
          path            相对 report_dir 的路径（'charts/xxx.png' / 'charts/xxx.mmd' / 'charts/xxx.html'）
          type            'png' | 'mermaid' | 'html'（html 为 ITEM-16 交互式 plotly 图）
          source          来源工件键名
          caption         人类可读英文标题
          placement_hint  报告装配语义锚（'scenarios'/'timeline'/'actors'/... 供钩子放置）

        总开关 REPORT_VISUALIZER 关闭 → 返回 [] 且不落盘（degrade-safe）。"""
        manifest: List[Dict[str, str]] = []
        if not bool(_cfg("REPORT_VISUALIZER", True)):
            return manifest
        artifacts = artifacts or {}
        charts_dir = os.path.join(report_dir, "charts")

        # ---- (A) Mermaid 族（零依赖，可独立于 matplotlib）----
        if bool(_cfg("REPORT_VIZ_MERMAID", True)):
            mermaid_specs = [
                ("timeline", self.render_mermaid_timeline(artifacts.get("timeline")),
                 "timeline", "Event Timeline", "timeline"),
                ("causal", self.render_mermaid_causal(artifacts.get("causal_paths")),
                 "causal_paths", "Causal Path Diagram", "drivers"),
                ("coalition", self.render_mermaid_coalition(artifacts.get("coalition")),
                 "coalition", "Coalition Map", "actors"),
                ("actor_network", self.render_mermaid_actor_network(artifacts.get("actors")),
                 "actors", "Actor Relationship Network", "actors"),
            ]
            for stem, block, source, caption, hint in mermaid_specs:
                if not block:
                    continue
                rel = self._write_mermaid(charts_dir, stem, block)
                if rel:
                    manifest.append({
                        "path": rel, "type": "mermaid", "source": source,
                        "caption": caption, "placement_hint": hint,
                    })

        # ---- (B) matplotlib 族（matplotlib 缺失/关闭 → 整族跳过）----
        if self._chart_ok():
            forecast = artifacts.get("forecast")
            chart_specs = [
                (self.build_scenario_bars(forecast, charts_dir),
                 "forecast", "Scenario Probabilities", "scenarios"),
                (self.build_model_vs_market(forecast, charts_dir),
                 "forecast", "Model vs Market", "binary_forecasts"),
                (self.build_worldstate_area(artifacts.get("world_state_trajectory"), charts_dir),
                 "world_state_trajectory", "Modeled Outcome-Share Trajectory", "simulation"),
                (self.build_comparison_bars(artifacts.get("comparison"), charts_dir),
                 "comparison", "Baseline vs Scenario", "comparison"),
                (self.build_calibration_curve(artifacts.get("calibration"), charts_dir),
                 "calibration", "Calibration Curve", "calibration"),
            ]
            for rel, source, caption, hint in chart_specs:
                if rel:
                    manifest.append({
                        "path": rel, "type": "png", "source": source,
                        "caption": caption, "placement_hint": hint,
                    })

        # ---- (B2) PM-6：市场价格历史折线（可选工件，缺失静默跳过）----
        # 数据源：artifacts['market_price_history'] 或 reports/{id}/market_price_history.json /
        # handoff/market_price_history.json；锚点取 forecast.binary_forecasts。缺任一即整族跳过。
        if self._chart_ok() and self._price_hist_ok():
            price_history = self._load_price_history(report_dir, artifacts)
            fc = artifacts.get("forecast")
            ph_anchors = fc.get("binary_forecasts") if isinstance(fc, dict) else None
            if isinstance(price_history, dict) and price_history and isinstance(ph_anchors, list):
                for rel in self.render_market_price_history(price_history, ph_anchors, charts_dir):
                    manifest.append({
                        "path": rel, "type": "png", "source": "market_price_history",
                        "caption": "Market-Implied P(yes) History vs Model",
                        "placement_hint": "binary_forecasts",
                    })

        # ---- (C) ITEM-16：plotly 交互式 HTML 族（plotly 缺失/关闭 → 整族跳过）----
        # 与 (B) matplotlib PNG 族并存：同一工件同时产出 PNG（PDF 内嵌）与 HTML（Web 交互）。
        # plotly 缺失或 REPORT_VIZ_INTERACTIVE=False 时该族完全不生成（degrade-safe）。
        if self._interactive_ok():
            fc_html = artifacts.get("forecast")
            html_specs = [
                (self.build_scenario_bars_html(fc_html, charts_dir),
                 "forecast", "Scenario Probabilities (interactive)", "scenarios"),
                (self.build_model_vs_market_html(fc_html, charts_dir),
                 "forecast", "Model vs Market (interactive)", "binary_forecasts"),
                (self.build_worldstate_area_html(artifacts.get("world_state_trajectory"), charts_dir),
                 "world_state_trajectory", "Modeled Outcome-Share Trajectory (interactive)", "simulation"),
            ]
            for rel, source, caption, hint in html_specs:
                if rel:
                    manifest.append({
                        "path": rel, "type": "html", "source": source,
                        "caption": caption, "placement_hint": hint,
                    })
            # ITEM-16 + PM-6：市场价格历史交互式 HTML（复用 (B2) 的 price_history 加载与锚点）。
            if self._price_hist_ok():
                ph_html = self._load_price_history(report_dir, artifacts)
                ph_anchors_html = fc_html.get("binary_forecasts") if isinstance(fc_html, dict) else None
                if isinstance(ph_html, dict) and ph_html and isinstance(ph_anchors_html, list):
                    for rel in self.render_market_price_history_html(ph_html, ph_anchors_html, charts_dir):
                        manifest.append({
                            "path": rel, "type": "html", "source": "market_price_history",
                            "caption": "Market-Implied P(yes) History vs Model (interactive)",
                            "placement_hint": "binary_forecasts",
                        })

        # ---- 落盘 manifest（原子写；失败不影响已生成的图表）----
        self._persist_manifest(report_dir, manifest)
        return manifest

    # ============================ 落盘助手 ============================

    @staticmethod
    def _write_mermaid(charts_dir: str, stem: str, block: str) -> Optional[str]:
        """把 Mermaid 代码块写到 charts/<stem>.mmd（含 ```mermaid 围栏，便于报告直接内联）。
        返回相对路径 'charts/<stem>.mmd'；失败 → None。"""
        try:
            os.makedirs(charts_dir, exist_ok=True)
            fname = f"{stem}.mmd"
            path = os.path.join(charts_dir, fname)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(block)
            os.replace(tmp, path)
            return os.path.join("charts", fname)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _persist_manifest(report_dir: str, manifest: List[Dict[str, str]]) -> None:
        """原子写 reports/{id}/viz_manifest.json。目录不可写等失败静默忽略（degrade-safe）。"""
        try:
            os.makedirs(report_dir, exist_ok=True)
            path = os.path.join(report_dir, "viz_manifest.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _load_price_history(report_dir: str, artifacts: Dict[str, Any]) -> Dict[str, Any]:
        """PM-6：汇集市场价格历史 {market_id:[{t,p}]}。优先级：artifacts['market_price_history']
        （dict，已加载）> reports/{id}/market_price_history.json > handoff/market_price_history.json
        （artifacts['handoff_dir'] 指定）。同 market_id 以高优先级为准，文件仅补缺键。任何缺失/
        畸形静默跳过（degrade-safe），全无 → {}。"""
        merged: Dict[str, Any] = {}

        def _absorb(d: Any) -> None:
            if isinstance(d, dict):
                for k, v in d.items():
                    key = str(k)
                    if key not in merged and isinstance(v, list):
                        merged[key] = v

        arts = artifacts or {}
        # ① artifacts 内联 dict 最高优先，先 absorb。
        _absorb(arts.get("market_price_history"))
        # ② 文件来源（仅补 artifacts 缺失的 market_id）。
        candidates: List[str] = [os.path.join(report_dir or "", "market_price_history.json")]
        hd = arts.get("handoff_dir")
        if isinstance(hd, str) and hd.strip():
            candidates.append(os.path.join(hd, "market_price_history.json"))
        for path in candidates:
            try:
                if path and os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        _absorb(json.load(f))
            except Exception:  # noqa: BLE001
                continue  # 单个文件坏了不影响其余来源
        return merged


# ─────────────────────────────────────────────────────────────────────────────
# 模块级解析助手（供 Mermaid/图表族共享，纯函数）
# ─────────────────────────────────────────────────────────────────────────────
# 'A --[CAUSES,+,strong]--> B' 形式的因果字符串解析正则。
_CAUSAL_STR_RE = re.compile(r"^\s*(.+?)\s*--\[(.*?)\]-->\s*(.+?)\s*$")


def _parse_causal_item(item: Any) -> List[Tuple[str, str, str]]:
    """把单个因果项解析成 [(source, target, label)]。支持：
      · 字符串 'A --[CAUSES,+,strong]--> B'（方括号内逗号分隔 类型,符号,强度）；
      · 字符串 'A --[CAUSES]--> B'（仅类型）；
      · dict {source,target, relation/type, sign, strength}；
      · dict {path:[a,b,c], relation:...}（多跳，两两成边）。
    无法解析 → []。"""
    try:
        if isinstance(item, str):
            m = _CAUSAL_STR_RE.match(item)
            if not m:
                return []
            src, inner, tgt = m.group(1), m.group(2), m.group(3)
            parts = [p.strip() for p in inner.split(",") if p.strip()]
            typ = parts[0].upper() if parts else "REL"
            label = _REL_LABEL_EN.get(typ, typ)
            extras = parts[1:]
            sign = ""
            strength = ""
            for e in extras:
                el = e.lower()
                if el in _SIGN_TOKEN:
                    sign = _SIGN_TOKEN[el]
                else:
                    strength = e
            tail = "/".join(x for x in (sign, strength) if x)
            full = f"{label} {tail}".strip() if tail else label
            return [(src.strip(), tgt.strip(), full)]

        if isinstance(item, dict):
            # 多跳 path
            path = item.get("path") or item.get("nodes")
            typ = str(item.get("relation") or item.get("type") or item.get("rel") or "").strip().upper()
            label = _REL_LABEL_EN.get(typ, typ or "REL")
            sign = _sign_of(item)
            strength = str(item.get("strength") or "").strip()
            tail = "/".join(x for x in (sign, strength) if x)
            full = f"{label} {tail}".strip() if tail else label
            if isinstance(path, list) and len(path) >= 2:
                edges: List[Tuple[str, str, str]] = []
                for a, b in zip(path, path[1:]):
                    sa, sb = str(a).strip(), str(b).strip()
                    if sa and sb:
                        edges.append((sa, sb, full))
                return edges
            src = str(item.get("source") or item.get("from") or "").strip()
            tgt = str(item.get("target") or item.get("to") or "").strip()
            if src and tgt:
                return [(src, tgt, full)]
        return []
    except Exception:  # noqa: BLE001
        return []


def _normalize_clusters(coalition: Any) -> List[Tuple[str, List[str]]]:
    """把灵活的派系入参规整为 [(label, [members])]。无有效派系 → []。"""
    try:
        if isinstance(coalition, dict):
            clusters = (coalition.get("clusters") or coalition.get("coalitions")
                        or coalition.get("factions") or coalition.get("groups"))
        else:
            clusters = coalition
        if not isinstance(clusters, list) or not clusters:
            return []
        out: List[Tuple[str, List[str]]] = []
        for i, c in enumerate(clusters, 1):
            if isinstance(c, dict):
                members = c.get("members") or c.get("agents") or c.get("names") or []
                label = str(c.get("label") or c.get("name") or f"Faction {i}")
            elif isinstance(c, (list, tuple, set)):
                members = list(c)
                label = f"Faction {i}"
            else:
                continue
            members = [str(m) for m in members if str(m).strip()]
            if members:
                out.append((label, members))
        return out
    except Exception:  # noqa: BLE001
        return []


def _first_float(d: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    """按 keys 顺序取第一个可解析为 float 的值。全部缺失/不可解析 → None。"""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            v = _to_float(d.get(k))
            if v is not None:
                return v
    return None


def _normalize_price_anchors(anchors: Any,
                             price_history: Dict[str, Any]) -> List[Dict[str, Any]]:
    """PM-6：把锚点列表规整为可绘制条目（供 render_market_price_history）。每项抽出
    {market_id, label, model_p, market_p, divergence, series}，其中 series 为按时间升序的
    [(datetime, p)] 列表（p 夹逼到 [0,1]）。

    锚点项兼容两种形态：① 二元预测 dict（含 market_anchor 子 dict）；② 已抽平 dict
    （market_id/probability/... 直接在顶层）。仅保留 market_id 命中 price_history 且有效历史点
    ≥2 的锚点，按首次出现顺序去重（确定性）。model_p 取二元预测的 probability；market_p 取
    market_anchor.implied_yes_prob，缺失则用最新历史点；divergence 取 market_anchor.divergence，
    缺失则由 model_p − market_p 现算。任何畸形 → 跳过该项，绝不抛。"""
    if not isinstance(anchors, list):
        return []
    import datetime as _dt
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for it in anchors:
        if not isinstance(it, dict):
            continue
        anchor = it.get("market_anchor") if isinstance(it.get("market_anchor"), dict) else it
        market_id = str(anchor.get("market_id") or it.get("market_id") or "").strip()
        if not market_id or market_id in seen:
            continue
        raw_series = price_history.get(market_id)
        if not isinstance(raw_series, list) or len(raw_series) < 2:
            continue
        pts: List[Tuple[Any, float]] = []
        for pt in raw_series:
            if not isinstance(pt, dict):
                continue
            tf = _to_float(pt.get("t"))
            p = _to_float(pt.get("p"))
            if tf is None or p is None:
                continue
            try:
                dt = _dt.datetime.utcfromtimestamp(tf)
            except (OverflowError, OSError, ValueError):
                continue  # 病态时间戳跳过
            pts.append((dt, max(0.0, min(1.0, p))))
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x[0])
        model_p = _first_float(it, ("probability", "model_prob", "model_p", "p"))
        market_p = _first_float(anchor, ("implied_yes_prob", "implied_prob"))
        if market_p is None:
            market_p = pts[-1][1]  # 缺市场概率 → 用最新历史点近似
        divergence = _first_float(anchor, ("divergence",))
        if divergence is None and model_p is not None and market_p is not None:
            divergence = model_p - market_p
        label = _mpl_text(it.get("id") or it.get("statement") or it.get("label") or market_id,
                          fallback=market_id, max_len=52)
        out.append({"market_id": market_id, "label": label, "model_p": model_p,
                    "market_p": market_p, "divergence": divergence, "series": pts})
        seen.add(market_id)
    return out
