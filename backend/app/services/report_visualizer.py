"""确定性报告可视化器（VIZ-1 → WAVE9 plotly-first 重建）—— 无 LLM、纯数据驱动，把已落盘的
结构化工件（forecast / ensemble_forecast / timeline / actors / quantitative / sources /
contested / graph_priors / world_state_trajectory / comparison / market_price_history /
校准账本统计）渲染成三族可视化：

  (A) Mermaid 纯函数助手（保留为库函数，build_all 不再产出 mermaid——旧 mermaid 时间线/
      角色网络在前端/PDF 均无法渲染且字符规整会毁文本，已由 plotly 等价图取代）。
  (B) matplotlib PNG（Agg 后端，可选依赖）——作为 plotly/kaleido 缺失或 PNG 导出失败时的
      降级回退族，另含 plotly 无等价图的对比分组柱与校准曲线。
  (C) plotly 交互式 HTML + kaleido 静态 PNG 对（主族）——情景概率误差棒（吃 ensemble
      stdev/min-max）、二元预测点阵、模型 vs 市场哑铃、时间线泳道、角色关系网络（networkx
      spring 布局）、角色影响力×显著度气泡、来源构成 sunburst、量化断言点阵、驱动因子
      tornado、市场价格历史折线、争议断言哑铃、世界态堆叠面积。每图为自包含
      charts/<name>.html（plotly.js 内联，完全离线可开）+ charts/<name>.png（kaleido，
      scale=2、宽 1200，manifest 项挂 png_path）。

设计约束（与 report_agent 的确定性工具族一致）：
  · 纯函数式的 render_mermaid_* 助手：入参是普通 dict/list，返回 markdown 字符串；
    任何畸形输入 → ''（绝不抛异常）。
  · 可选依赖全部模块级探测（MATPLOTLIB_AVAILABLE / PLOTLY_AVAILABLE / NETWORKX_AVAILABLE /
    KALEIDO_AVAILABLE），缺失即整族/单图降级，绝不阻断报告生成。
  · build_all 返回 manifest 项列表（[{id,path,type,title,caption,source,placement_hint,
    png_path?}]），并把 {"schema_version":2,"items":[...],"skipped":[{builder,reason}]}
    落盘 reports/{id}/viz_manifest.json——跳过不再沉默：每个未产出的构建器都有 skipped 记录，
    并打一条 INFO 汇总日志（produced vs skipped）。
  · 所有输出确定性：相同工件 → 相同图表数据（networkx 布局固定 seed）。中文+英文双语注释，
    图表文案统一英文、字体栈带 CJK 回退（EN/ZH 数据均可渲染）。

env 旋钮（Config，全部 degrade-safe 默认）：
  REPORT_VISUALIZER      主开关（默认开）
  REPORT_VIZ_MERMAID     Mermaid 族开关（保留兼容；build_all 已不产 mermaid，仅影响库函数调用方）
  REPORT_VIZ_CHARTS      matplotlib PNG 回退族开关（默认开；matplotlib 缺失时自动无效）
  REPORT_VIZ_DPI         matplotlib PNG dpi（默认 160）
  REPORT_VIZ_MAX_NODES   通用节点/锚点上限（默认 40）
  REPORT_VIZ_PRICE_HISTORY  市场价格历史折线族开关（默认开）
  REPORT_VIZ_INTERACTIVE plotly 主族开关（默认开；plotly 缺失时自动无效）
  REPORT_VIZ_PNG_EXPORT  kaleido 静态 PNG 导出开关（默认开；kaleido 缺失/失败自动回退）
  REPORT_VIZ_TIMELINE_MAX_EVENTS  时间线泳道事件上限（默认 40）
  REPORT_VIZ_NETWORK_MAX_NODES    角色网络节点上限（默认 60）
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
# networkx 可选依赖（WAVE9 角色网络 spring 布局）：缺失 → 确定性同心圆回退布局。
# ─────────────────────────────────────────────────────────────────────────────
try:  # pragma: no cover
    import networkx as nx  # noqa: E402
    NETWORKX_AVAILABLE = True
except Exception:  # noqa: BLE001
    nx = None  # type: ignore
    NETWORKX_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# kaleido 可选依赖（WAVE9 plotly → 静态 PNG 导出）：只探测可导入性，不在 import 期启动
# Chromium。运行期渲染失败会把 _KALEIDO_RUNTIME_OK 置 False（进程内一次性熔断，避免每图重试
# 各等一次超时），并回退 matplotlib（有等价图时）。
# ─────────────────────────────────────────────────────────────────────────────
try:  # pragma: no cover
    import importlib.util as _ilu
    KALEIDO_AVAILABLE = _ilu.find_spec("kaleido") is not None
except Exception:  # noqa: BLE001
    KALEIDO_AVAILABLE = False

_KALEIDO_RUNTIME_OK = True  # 运行期熔断标志（首个渲染异常后本进程不再尝试 PNG 导出）


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


# ─────────────────────────────────────────────────────────────────────────────
# WAVE9 统一图表风格（clean light surface + 浅灰网格 + CVD-safe 分类色板 + CJK 字体回退栈）。
# 色板与角色遵循 dataviz 参考色板：分类槽位固定顺序（不可循环生成）、正/负/中性边色用
# 发散对（蓝↔红）+ 中性灰、状态色（stale/unreachable）与分类色分离。
# ─────────────────────────────────────────────────────────────────────────────
_VIZ_FONT = ('system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Arial, '
             '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", '
             'sans-serif')
_PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
            "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
_SEQ_BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]
_COLOR_MODEL = "#2a78d6"    # 模型概率（分类槽 1 蓝）
_COLOR_MARKET = "#e34948"   # 市场隐含概率（发散对红极）
_COLOR_POS = "#1baf7a"      # 正向/结盟边
_COLOR_NEG = "#e34948"      # 负向/对抗边
_COLOR_NEU = "#b3b1a9"      # 中性/交易型边
_COLOR_STALE = "#d03b3b"    # 状态色 critical（陈旧/不可达标记，不与分类色混用）
_COLOR_GOOD = "#0ca30c"     # 状态色 good（可达）
_SURFACE = "#fcfcfb"
_GRID = "#e1e0d9"
_AXIS = "#c3c2b7"
_INK = "#0b0b0b"
_INK_2 = "#52514e"

# 影响力/显著度分级 → 数值（气泡图 y 轴与网络图节点排序共用）。
_LEVEL_NUM = {"very high": 4.0, "high": 3.0, "medium": 2.0, "med": 2.0,
              "moderate": 2.0, "low": 1.0, "very low": 0.5}

# 证据层级 → 序数权重（S1 最强）。用于量化断言排序/着色与争议断言证据权重。
_TIER_RANK = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
_TIER_COLOR = {"S1": "#184f95", "S2": "#3987e5", "S3": "#9ec5f4", "S4": "#cde2fb"}
_TIER_WEIGHT = {"S1": 3.0, "S2": 2.0, "S3": 1.0, "S4": 0.5}


def _apply_layout(fig, title: str, height: int = 520, **kwargs) -> None:
    """统一 plotly 布局：浅色画布 + 细网格 + 固定分类色板 + CJK 安全字体栈。就地修改 fig。"""
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color=_INK, family=_VIZ_FONT), x=0.02),
        font=dict(family=_VIZ_FONT, size=12, color=_INK_2),
        paper_bgcolor=_SURFACE, plot_bgcolor=_SURFACE,
        colorway=_PALETTE, height=height,
        margin=dict(l=10, r=24, t=58, b=46),
        hoverlabel=dict(font=dict(family=_VIZ_FONT, size=12)),
        **kwargs,
    )
    fig.update_xaxes(gridcolor=_GRID, zerolinecolor=_AXIS, linecolor=_AXIS,
                     tickfont=dict(size=11))
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=_AXIS, linecolor=_AXIS,
                     tickfont=dict(size=11))


def _wrap_hover(text: Any, width: int = 64, max_len: int = 480) -> str:
    """把长文本按词折行成 <br> 分隔的 hover 文案（HTML 转义 <>&）。空 → ''。"""
    s = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    if not s:
        return ""
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 成对 '$' 防 MathJax（见 _html_text）；须在 & 转义之后（实体自身含 &）。
    if s.count("$") >= 2:
        s = s.replace("$", "&#36;")
    words = s.split(" ")
    lines: List[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "<br>".join(lines)


def _tokens(text: Any) -> set:
    """小写字母数字词元集合（时间线/驱动因子近重合并用）。"""
    return set(re.findall(r"[a-z0-9]{2,}", str(text or "").lower()))


def _jaccard(a: set, b: set) -> float:
    """两个词元集合的 Jaccard 相似度；任一为空 → 0。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a | b))


def _san_label(text: Any, max_len: int = 80) -> str:
    """规整 Mermaid 引号标签内的文本：去掉会破坏语法的字符（双引号/竖线/换行/尖括号），
    折叠空白并截断。空/None → ''。"""
    s = str(text if text is not None else "").strip()
    if not s:
        return ""
    s = s.replace('"', "'").replace("|", "/").replace("\n", " ").replace("\r", " ")
    # 尖括号/方括号会破坏 Mermaid 语法，但旧实现把 '>' 换成 ')' 会毁掉 '>$1T' 这类文本——
    # 改用全角等价字形（渲染语义不变、Mermaid 安全）。
    s = s.replace("<", "＜").replace(">", "＞").replace("[", "(").replace("]", ")")
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
    # WAVE9：成对 '$' 会触发 matplotlib mathtext（'$100B vs $65B' 变斜体数学式），反斜杠转义。
    if kept.count("$") >= 2:
        kept = kept.replace("$", r"\$")
    return kept


def _html_text(text: Any, fallback: str = "", max_len: int = 60) -> str:
    """plotly HTML 标签的文本规整（ITEM-16）：先套用已知中文维度→英文映射（与图注一致），但
    保留 CJK/非拉丁字形（浏览器字体可渲染，无缺字方块问题），仅折叠空白并截断。空 → fallback。
    WAVE9：成对 '$' 会被 plotly/MathJax 当 LaTeX 数学式渲染（'$100B vs $65B' 变斜体乱排），
    改写为 HTML 实体 &#36;（显示不变、MathJax 不再匹配）。"""
    s = str(text if text is not None else "").strip()
    s = _COMPARISON_DIM_EN.get(s, s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return fallback
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    if s.count("$") >= 2:
        s = s.replace("$", "&#36;")
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
    build_all 文档字符串。任何工件缺失/畸形都跳过对应图（并记入 manifest 的 skipped 列表），
    绝不阻断报告生成。
    """

    def __init__(self) -> None:
        # WAVE9 PNG 导出批处理状态：build_all 内先排队、末尾一次 flush（kaleido/Chromium 启动
        # 摊销到一次）；独立调用构建器时即时渲染。
        self._png_jobs: List[Tuple[Any, str, str, str]] = []  # (fig, abs_path, rel_path, item_id)
        self._png_done: Dict[str, str] = {}                   # item_id -> rel png path
        self._batch_mode = False
        self._kaleido_failed = False

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

    def build_scenario_bars(self, forecast: Any, charts_dir: str,
                            ensemble: Any = None) -> Optional[str]:
        """(1) 情景概率横向柱状 + 误差棒。误差带优先来自 ensemble_forecast.json 的
        stdev/min/max（WAVE9 修复：旧版只认 forecast.json 的 p_low/p_high，而该字段实际
        总为 None → 光杆柱状），无 ensemble 时回退 forecast scenarios 的区间键。无情景 → None。"""
        if not self._chart_ok():
            return None
        try:
            rows = _extract_scenario_rows(forecast, ensemble)
            if not rows:
                return None
            names: List[str] = []
            probs: List[float] = []
            lo_err: List[float] = []
            hi_err: List[float] = []
            has_err = False
            for i, r in enumerate(rows, 1):
                nm = _mpl_text(r["name"], fallback=f"Scenario {i}", max_len=48)
                names.append(nm)
                probs.append(r["p"])
                if r["lo"] is not None and r["hi"] is not None:
                    lo_err.append(max(0.0, r["p"] - r["lo"]))
                    hi_err.append(max(0.0, r["hi"] - r["p"]))
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
            ax.set_title("Scenario Probabilities" + (" (ensemble spread)" if has_err else ""),
                         fontsize=12, fontweight="bold")
            for yi, p in zip(y, probs):
                ax.text(p + 0.01, yi, f"{p * 100:.0f}%", va="center", fontsize=9)
            ax.grid(axis="x", linestyle=":", alpha=0.4)
            fig.tight_layout()
            return self._save(fig, charts_dir, "scenario_probabilities.png")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_scenario_bars 失败（跳过该图）：%s", exc)
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

    def _png_export_ok(self) -> bool:
        """WAVE9：kaleido 静态 PNG 导出开关（默认开）。kaleido 缺失或运行期已熔断 → False。"""
        return (KALEIDO_AVAILABLE and _KALEIDO_RUNTIME_OK
                and bool(_cfg("REPORT_VIZ_PNG_EXPORT", True)))

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
        except Exception as exc:  # noqa: BLE001 - 落盘失败不阻断其余可视化
            logger.debug("HTML 图落盘失败 %s：%s", filename, exc)
            return None

    def _save_png(self, fig, path: str) -> bool:
        """WAVE9：单张 plotly figure → 静态 PNG（kaleido，scale=2、宽 1200px，高取 fig 布局高）。
        成功 → True；任何失败 → False 并置进程级熔断标志（后续图不再逐个等待超时）。"""
        global _KALEIDO_RUNTIME_OK
        if not self._png_export_ok():
            return False
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            height = None
            try:
                height = int(fig.layout.height) if fig.layout.height else None
            except Exception:  # noqa: BLE001
                height = None
            fig.write_image(path, scale=2, width=1200, height=height)
            return os.path.exists(path)
        except Exception as exc:  # noqa: BLE001
            _KALEIDO_RUNTIME_OK = False
            self._kaleido_failed = True
            logger.warning("kaleido PNG 导出失败（本进程熔断，回退 matplotlib/仅 HTML）：%s", exc)
            return False

    def _queue_png(self, fig, charts_dir: str, stem: str, item_id: str) -> None:
        """WAVE9：登记一张待导出 PNG。build_all 批处理模式下排队（末尾一次 flush，摊销 Chromium
        启动），独立调用构建器时即时渲染。成功的 item_id → 相对路径记入 self._png_done。"""
        if not self._png_export_ok():
            return
        abs_path = os.path.join(charts_dir, f"{stem}.png")
        rel_path = os.path.join("charts", f"{stem}.png")
        if self._batch_mode:
            self._png_jobs.append((fig, abs_path, rel_path, item_id))
        elif self._save_png(fig, abs_path):
            self._png_done[item_id] = rel_path

    def _flush_png_jobs(self) -> None:
        """WAVE9：批量导出排队中的 PNG。优先 plotly.io.write_images（单次 Chromium 会话渲染全部
        图），不可用/整批失败时回退逐张 write_image；单张失败只丢那一张。"""
        global _KALEIDO_RUNTIME_OK
        jobs, self._png_jobs = self._png_jobs, []
        if not jobs or not self._png_export_ok():
            return
        try:
            import plotly.io as pio
            figs = [j[0] for j in jobs]
            paths = [j[1] for j in jobs]
            heights = []
            for f in figs:
                try:
                    heights.append(int(f.layout.height) if f.layout.height else 600)
                except Exception:  # noqa: BLE001
                    heights.append(600)
            pio.write_images(figs, paths, scale=2, width=1200, height=heights)
            for _, abs_path, rel_path, item_id in jobs:
                if os.path.exists(abs_path):
                    self._png_done[item_id] = rel_path
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("kaleido 批量 PNG 导出不可用（%s），回退逐张导出", exc)
        # 逐张回退（_save_png 内部有熔断：首张失败后整体停止尝试）。
        for fig, abs_path, rel_path, item_id in jobs:
            if not self._png_export_ok():
                break
            if self._save_png(fig, abs_path):
                self._png_done[item_id] = rel_path

    def _save_pair(self, fig, charts_dir: str, stem: str,
                   item_id: Optional[str] = None) -> Optional[str]:
        """WAVE9：HTML+PNG 成对落盘——HTML 即时写，PNG 排队/即时导出（见 _queue_png）。
        返回 HTML 相对路径（保持既有构建器签名不变）；PNG 路径经 self._png_done[item_id] 查询。"""
        rel = self._save_html(fig, charts_dir, f"{stem}.html")
        if rel:
            self._queue_png(fig, charts_dir, stem, item_id or stem)
        return rel

    def build_scenario_bars_html(self, forecast: Any, charts_dir: str,
                                 ensemble: Any = None) -> Optional[str]:
        """(C1) 情景概率横向柱 + 误差棒（HTML+PNG 对）。误差带优先取 ensemble_forecast.json 的
        min/max（hover 附 stdev / 支持率），无 ensemble 时回退 forecast scenarios 的
        p_low/p_high。无情景 → None。"""
        if not self._interactive_ok():
            return None
        try:
            rows = _extract_scenario_rows(forecast, ensemble)
            if not rows:
                return None
            names = [r["name"] for r in rows]
            probs = [r["p"] for r in rows]
            has_err = any(r["lo"] is not None and r["hi"] is not None for r in rows)
            err_x = None
            if has_err:
                err_x = dict(
                    type="data", symmetric=False,
                    array=[max(0.0, (r["hi"] - r["p"])) if r["hi"] is not None else 0.0
                           for r in rows],
                    arrayminus=[max(0.0, (r["p"] - r["lo"])) if r["lo"] is not None else 0.0
                                for r in rows],
                    color=_INK_2, thickness=1.4, width=5,
                )
            hover = []
            for r in rows:
                parts = [f"<b>{_wrap_hover(r['name'], 48)}</b>", f"P = {r['p']:.1%}"]
                if r["lo"] is not None and r["hi"] is not None:
                    parts.append(f"range {r['lo']:.1%} – {r['hi']:.1%}")
                if r.get("stdev") is not None:
                    parts.append(f"ensemble stdev {r['stdev']:.3f}")
                if r.get("support") is not None:
                    parts.append(f"support ratio {r['support']:.0%}")
                hover.append("<br>".join(parts))
            fig = go.Figure(go.Bar(
                x=probs, y=names, orientation="h", marker_color=_COLOR_MODEL,
                error_x=err_x,
                text=[f"{p * 100:.0f}%" for p in probs], textposition="outside",
                hovertext=hover, hoverinfo="text",
            ))
            title = "Scenario Probabilities" + (" (ensemble spread)" if has_err else "")
            _apply_layout(fig, title, height=max(320, 42 * len(names) + 140))
            fig.update_layout(
                xaxis_title="Probability",
                xaxis=dict(range=[0, max(1.0, max(probs) * 1.2)], tickformat=".0%"),
                yaxis=dict(autorange="reversed"),  # 概率最高的情景显示在顶部
            )
            return self._save_pair(fig, charts_dir, "scenario_probabilities",
                                   "scenario_probabilities")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_scenario_bars_html 失败（跳过该图）：%s", exc)
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
                    line=dict(color=_COLOR_NEU, width=2),
                    showlegend=False, hoverinfo="skip",
                ))
            fig.add_trace(go.Scatter(
                x=market_p, y=labels, mode="markers", name="Market implied",
                marker=dict(color=_COLOR_MARKET, size=11,
                            line=dict(color=_SURFACE, width=2)),
                hovertemplate="%{y} — market: %{x:.1%}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=model_p, y=labels, mode="markers", name="Model",
                marker=dict(color=_COLOR_MODEL, size=11,
                            line=dict(color=_SURFACE, width=2)),
                hovertemplate="%{y} — model: %{x:.1%}<extra></extra>",
            ))
            _apply_layout(fig, "Model vs Market (binary forecasts)",
                          height=max(320, 40 * len(labels) + 150))
            fig.update_layout(
                xaxis_title="P(yes)", xaxis=dict(range=[0, 1], tickformat=".0%"),
                yaxis=dict(autorange="reversed"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "model_vs_market", "model_vs_market")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_model_vs_market_html 失败（跳过该图）：%s", exc)
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
                    line=dict(width=1),
                    hovertemplate="round %{x}: %{y:.2f}<extra>" + _html_text(nm, max_len=40) + "</extra>",
                ))
            _apply_layout(fig, "Modeled Outcome-Share Trajectory", height=460)
            fig.update_layout(xaxis_title="Simulation round", yaxis_title="Outcome share")
            return self._save_pair(fig, charts_dir, "worldstate_trajectory",
                                   "worldstate_trajectory")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_worldstate_area_html 失败（跳过该图）：%s", exc)
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
                    line=dict(color=_COLOR_MARKET, width=2), marker=dict(size=5),
                    hovertemplate="%{x|%Y-%m-%d}: %{y:.1%}<extra></extra>",
                ))
                mp = a["model_p"]
                if mp is not None:
                    fig.add_hline(y=mp, line_dash="dash", line_color=_COLOR_MODEL,
                                  annotation_text=f"Model P(yes) = {mp * 100:.0f}%",
                                  annotation_position="top left")
                div = a["divergence"]
                if div is not None:
                    fig.add_annotation(
                        xref="paper", yref="paper", x=0.02, y=0.04, showarrow=False,
                        text=f"Divergence (model − market): {div * 100:+.0f} pp",
                        bgcolor="#f2f2f2", bordercolor=_COLOR_NEU, borderwidth=1,
                        font=dict(size=11, color=_INK_2),
                    )
                _apply_layout(fig, a["label"], height=420)
                fig.update_layout(
                    xaxis_title="Date", yaxis_title="P(yes)",
                    yaxis=dict(range=[0, 1], tickformat=".0%"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                stem = f"market_price_history_{gi}"
                rel = self._save_pair(fig, out_dir, stem, stem)
                if rel:
                    paths.append(rel)
            return paths
        except Exception as exc:  # noqa: BLE001
            logger.debug("render_market_price_history_html 失败（跳过该族）：%s", exc)
            return []

    # ==================== (D) WAVE9 新 plotly 构建器（HTML+PNG 对） ====================
    # 全部实例方法：入参普通 dict/list + 输出目录，成功返回 'charts/<name>.html' 相对路径，
    # 失败/无数据 → None（never raises，异常仅 debug 日志）。PNG 对经 _save_pair 排队导出。

    def build_binary_dotplot_html(self, forecast: Any, charts_dir: str) -> Optional[str]:
        """(D1) 二元预测点阵：全部 P(yes) 按概率降序，置信度着色（bf.confidence，缺失时用
        决断度 |p−0.5|×2 近似），带 market_anchor 的条目叠加市场隐含概率菱形 + 连线。"""
        if not self._interactive_ok():
            return None
        try:
            bfs = forecast.get("binary_forecasts") if isinstance(forecast, dict) else forecast
            if not isinstance(bfs, list) or not bfs:
                return None
            rows: List[Dict[str, Any]] = []
            for bf in bfs:
                if not isinstance(bf, dict):
                    continue
                p = _first_float(bf, ("probability", "prob", "p"))
                if p is None:
                    continue
                conf = _first_float(bf, ("confidence",))
                if conf is None or not (0.0 <= conf <= 1.0):
                    conf = min(1.0, abs(p - 0.5) * 2)  # 决断度近似
                anchor = bf.get("market_anchor")
                market = (_first_float(anchor, ("implied_yes_prob", "implied_prob"))
                          if isinstance(anchor, dict) else None)
                bid = _html_text(bf.get("id"), fallback=f"F{len(rows) + 1}", max_len=10)
                stmt = _html_text(bf.get("statement"), fallback=bid, max_len=64)
                rows.append({
                    "label": f"{bid} · {stmt}", "p": p, "conf": conf, "market": market,
                    "hover": "<br>".join(x for x in (
                        f"<b>{bid}</b> P(yes) = {p:.1%}",
                        _wrap_hover(bf.get("statement"), 60),
                        f"theme: {_html_text(bf.get('theme'), max_len=40)}"
                        if bf.get("theme") else "",
                        f"market implied: {market:.1%}" if market is not None else "",
                    ) if x),
                })
            if not rows:
                return None
            rows.sort(key=lambda r: (-r["p"], r["label"]))
            labels = [r["label"] for r in rows]
            fig = go.Figure()
            # 市场锚点连线 + 菱形（有锚点的条目才有）。
            anchored = [r for r in rows if r["market"] is not None]
            for r in anchored:
                fig.add_trace(go.Scatter(
                    x=[r["market"], r["p"]], y=[r["label"], r["label"]], mode="lines",
                    line=dict(color=_COLOR_NEU, width=1.6),
                    showlegend=False, hoverinfo="skip",
                ))
            if anchored:
                fig.add_trace(go.Scatter(
                    x=[r["market"] for r in anchored], y=[r["label"] for r in anchored],
                    mode="markers", name="Market implied",
                    marker=dict(symbol="diamond", color=_COLOR_MARKET, size=10,
                                line=dict(color=_SURFACE, width=2)),
                    hovertemplate="market: %{x:.1%}<extra></extra>",
                ))
            fig.add_trace(go.Scatter(
                x=[r["p"] for r in rows], y=labels, mode="markers", name="Model P(yes)",
                marker=dict(
                    size=11, color=[r["conf"] for r in rows], cmin=0.0, cmax=1.0,
                    colorscale=[[i / (len(_SEQ_BLUES) - 1), c]
                                for i, c in enumerate(_SEQ_BLUES)],
                    colorbar=dict(title=dict(text="Confidence", font=dict(size=11)),
                                  thickness=12, len=0.6, tickformat=".0%"),
                    line=dict(color=_SURFACE, width=2),
                ),
                hovertext=[r["hover"] for r in rows], hoverinfo="text",
            ))
            fig.add_vline(x=0.5, line_dash="dot", line_color=_AXIS)
            _apply_layout(fig, "Binary Forecasts — P(yes) with confidence",
                          height=max(340, 30 * len(rows) + 170))
            fig.update_layout(
                xaxis_title="P(yes)", xaxis=dict(range=[0, 1], tickformat=".0%"),
                yaxis=dict(autorange="reversed", categoryorder="array",
                           categoryarray=labels),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "binary_forecast_dotplot",
                                   "binary_forecast_dotplot")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_binary_dotplot_html 失败（跳过该图）：%s", exc)
            return None

    def build_timeline_lanes_html(self, timeline: Any, charts_dir: str) -> Optional[str]:
        """(D2) 时间线泳道图（取代旧 mermaid timeline）：x=日期、y=主题泳道（关键词归类），
        hover=完整事件文本（不截断、不毁字符），同日高词元重合事件去重，超上限
        （REPORT_VIZ_TIMELINE_MAX_EVENTS）按显著度截断后仍按时间升序。"""
        if not self._interactive_ok():
            return None
        try:
            rows = timeline.get("timeline") if isinstance(timeline, dict) else timeline
            if not isinstance(rows, list) or not rows:
                return None
            events = _prepare_timeline_events(rows)
            if not events:
                return None
            lanes = [c for c, _ in _TL_CATEGORIES] + ["Other"]
            lane_idx = {c: i for i, c in enumerate(lanes)}
            used = sorted({e["cat"] for e in events}, key=lambda c: lane_idx[c])
            fig = go.Figure()
            # 标注少量显著事件的短标签（其余仅 hover）——密集簇里标签必然相撞，宁少勿叠；
            # 同泳道内按序轮换四个文本方位进一步降碰撞。
            top_ids = {id(e) for e in sorted(events, key=lambda e: -e["score"])[:10]}
            _POSITIONS = ("top center", "bottom center", "middle right", "middle left")
            for cat in used:
                evs = [e for e in events if e["cat"] == cat]
                xs = [e["dt"] for e in evs]
                ys = [lane_idx[cat] + ((k % 3) - 1) * 0.18 for k in range(len(evs))]
                texts = []
                positions = []
                labeled = 0
                for e in evs:
                    if id(e) in top_ids:
                        texts.append(_html_text(e["text"], max_len=28))
                        positions.append(_POSITIONS[labeled % len(_POSITIONS)])
                        labeled += 1
                    else:
                        texts.append("")
                        positions.append("top center")
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="markers+text", name=cat,
                    text=texts, textposition=positions,
                    textfont=dict(size=9, color=_INK_2),
                    marker=dict(size=9, color=_PALETTE[lane_idx[cat] % len(_PALETTE)],
                                line=dict(color=_SURFACE, width=1.5)),
                    hovertext=[f"<b>{e['date']}</b><br>{_wrap_hover(e['text'], 64)}"
                               for e in evs],
                    hoverinfo="text",
                ))
            _apply_layout(fig, "Event Timeline", height=max(420, 90 * len(used) + 180))
            fig.update_layout(
                xaxis_title="Date",
                yaxis=dict(
                    tickvals=[lane_idx[c] for c in used], ticktext=used,
                    # 显式倒序区间（首个泳道在顶部）；不与 autorange 混用。
                    range=[max(lane_idx[c] for c in used) + 0.7, -0.7],
                    showgrid=True, zeroline=False,  # 泳道 0 不该有零线横贯
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "timeline_lanes", "timeline_lanes")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_timeline_lanes_html 失败（跳过该图）：%s", exc)
            return None

    def build_actor_network_html(self, actors: Any, charts_dir: str,
                                 graph_priors: Any = None) -> Optional[str]:
        """(D3) 角色关系网络（取代旧 mermaid actor_network）：networkx spring 布局（固定 seed，
        缺 networkx 时确定性圆布局），节点大小=graph_priors 权重（缺则度数），边色=关系符号
        （+绿/−红/±灰），端点名经 actors 列表归一（'Samsung'→'Samsung Electronics' 类重复合并），
        边按 (src,dst,type) 去重，节点上限 REPORT_VIZ_NETWORK_MAX_NODES。"""
        if not self._interactive_ok():
            return None
        try:
            rels = (actors.get("relationships") or actors.get("relations")
                    or actors.get("edges")) if isinstance(actors, dict) else actors
            if not isinstance(rels, list) or not rels:
                return None
            actor_list = actors.get("actors") if isinstance(actors, dict) else None
            canon = _canonical_actor_map(actor_list)
            priors: Dict[str, float] = {}
            if isinstance(graph_priors, dict):
                for k, v in graph_priors.items():
                    fv = _to_float(v)
                    if fv is not None and str(k).strip():
                        priors[_norm_key(k)] = fv
            # 边收集：端点归一 + (src,dst,type) 去重 + 自环剔除。
            seen_edges: set = set()
            edges: List[Tuple[str, str, str, str]] = []  # (src, dst, type, sign)
            node_order: List[str] = []
            degree: Dict[str, int] = {}
            for r in rels:
                if not isinstance(r, dict):
                    continue
                src = _canonicalize(str(r.get("source") or r.get("from") or "").strip(), canon)
                tgt = _canonicalize(str(r.get("target") or r.get("to") or "").strip(), canon)
                if not src or not tgt or src == tgt:
                    continue
                typ = str(r.get("type") or r.get("relation") or r.get("rel") or "REL").strip().upper()
                key = (src, tgt, typ)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append((src, tgt, typ, _sign_of(r) or "±"))
                for n in (src, tgt):
                    if n not in degree:
                        degree[n] = 0
                        node_order.append(n)
                    degree[n] += 1
            if not edges:
                return None
            # 节点上限：graph_priors 权重优先，其次度数，再按首现序（确定性）。
            max_nodes = int(_cfg("REPORT_VIZ_NETWORK_MAX_NODES", 60) or 60)
            first_seen = {n: i for i, n in enumerate(node_order)}
            ranked = sorted(node_order,
                            key=lambda n: (-priors.get(_norm_key(n), 0.0),
                                           -degree.get(n, 0), first_seen[n]))
            kept = set(ranked[:max(2, max_nodes)])
            edges = [e for e in edges if e[0] in kept and e[1] in kept]
            nodes = [n for n in node_order if n in kept]
            if not edges or len(nodes) < 2:
                return None
            pos = _network_layout(nodes, edges)
            # 角色元数据（role_class/influence → hover + 着色）。
            meta: Dict[str, Dict[str, Any]] = {}
            if isinstance(actor_list, list):
                for a in actor_list:
                    if isinstance(a, dict) and str(a.get("name") or "").strip():
                        meta[_norm_key(a["name"])] = a
            # 三类符号边各一 trace（None 分隔的折线集合）。
            sign_style = {"+": (_COLOR_POS, "supportive (+)"),
                          "−": (_COLOR_NEG, "adversarial (−)"),
                          "±": (_COLOR_NEU, "neutral (±)")}
            fig = go.Figure()
            for sign, (color, label) in sign_style.items():
                xs: List[Any] = []
                ys: List[Any] = []
                for src, tgt, _typ, s in edges:
                    if s != sign:
                        continue
                    x0, y0 = pos[src]
                    x1, y1 = pos[tgt]
                    xs.extend([x0, x1, None])
                    ys.extend([y0, y1, None])
                if not xs:
                    continue
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode="lines", name=label,
                    line=dict(color=color, width=1), opacity=0.55, hoverinfo="skip",
                ))
            # 节点：大小=priors 权重（缺则度数）归一，颜色=role_class 分类。
            weights = {}
            has_prior = any(_norm_key(n) in priors for n in nodes)
            for n in nodes:
                weights[n] = (priors.get(_norm_key(n), 0.0) if has_prior
                              else float(degree.get(n, 1)))
            wmax = max(weights.values()) or 1.0
            classes = sorted({str((meta.get(_norm_key(n)) or {}).get("role_class")
                                  or "other") for n in nodes})
            class_color = {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(classes)}
            label_top = set(sorted(nodes, key=lambda n: -weights[n])[:25])
            for cls in classes:
                cnodes = [n for n in nodes
                          if str((meta.get(_norm_key(n)) or {}).get("role_class")
                                 or "other") == cls]
                if not cnodes:
                    continue
                hovers = []
                for n in cnodes:
                    m = meta.get(_norm_key(n)) or {}
                    hovers.append("<br>".join(x for x in (
                        f"<b>{_html_text(n, max_len=60)}</b>",
                        f"role_class: {cls}",
                        f"influence: {_html_text(m.get('influence'), max_len=20)}"
                        if m.get("influence") else "",
                        f"degree: {degree.get(n, 0)}",
                        f"graph prior: {priors.get(_norm_key(n)):.3f}"
                        if _norm_key(n) in priors else "",
                    ) if x))
                fig.add_trace(go.Scatter(
                    x=[pos[n][0] for n in cnodes], y=[pos[n][1] for n in cnodes],
                    mode="markers+text", name=cls,
                    text=[_html_text(n, max_len=22) if n in label_top else ""
                          for n in cnodes],
                    textposition="top center", textfont=dict(size=10, color=_INK),
                    marker=dict(
                        size=[10 + 26 * (weights[n] / wmax) for n in cnodes],
                        color=class_color[cls], line=dict(color=_SURFACE, width=2),
                    ),
                    hovertext=hovers, hoverinfo="text",
                ))
            _apply_layout(fig, "Actor Relationship Network", height=720)
            fig.update_layout(
                xaxis=dict(visible=False), yaxis=dict(visible=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "actor_network", "actor_network")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_actor_network_html 失败（跳过该图）：%s", exc)
            return None

    def build_actor_bubble_html(self, actors: Any, charts_dir: str) -> Optional[str]:
        """(D4) 角色影响力×显著度气泡：x=salience score（0–1）、y=influence 分级（确定性微错位
        防重叠）、气泡大小=关系度数、颜色=role_class。"""
        if not self._interactive_ok():
            return None
        try:
            actor_list = actors.get("actors") if isinstance(actors, dict) else actors
            if not isinstance(actor_list, list) or not actor_list:
                return None
            rels = actors.get("relationships") if isinstance(actors, dict) else None
            degree: Dict[str, int] = {}
            if isinstance(rels, list):
                for r in rels:
                    if not isinstance(r, dict):
                        continue
                    for k in ("source", "target"):
                        n = _norm_key(str(r.get(k) or ""))
                        if n:
                            degree[n] = degree.get(n, 0) + 1
            rows: List[Dict[str, Any]] = []
            for i, a in enumerate(actor_list):
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "").strip()
                if not name:
                    continue
                sal = a.get("salience")
                if isinstance(sal, dict):
                    sx = _to_float(sal.get("score"))
                    if sx is None:
                        sx = (_LEVEL_NUM.get(str(sal.get("tier") or "").lower(), 2.0)) / 4.0
                else:
                    sx = _to_float(sal)
                    if sx is None:
                        sx = (_LEVEL_NUM.get(str(sal or "").lower(), 2.0)) / 4.0
                infl = _LEVEL_NUM.get(str(a.get("influence") or "").strip().lower(), 2.0)
                rows.append({
                    "name": name,
                    "x": max(0.0, min(1.0, sx)),
                    "y": infl + ((i % 5) - 2) * 0.09,  # 确定性微错位
                    "size": degree.get(_norm_key(name), 1),
                    "cls": str(a.get("role_class") or "other"),
                    "hover": "<br>".join(x for x in (
                        f"<b>{_html_text(name, max_len=60)}</b>",
                        _wrap_hover(a.get("role"), 60, max_len=240),
                        f"influence: {_html_text(a.get('influence'), max_len=20)} · "
                        f"salience: {sx:.2f} · degree: {degree.get(_norm_key(name), 0)}",
                    ) if x),
                })
            if not rows:
                return None
            smax = max(r["size"] for r in rows) or 1
            classes = sorted({r["cls"] for r in rows})
            fig = go.Figure()
            for ci, cls in enumerate(classes):
                sub = [r for r in rows if r["cls"] == cls]
                fig.add_trace(go.Scatter(
                    x=[r["x"] for r in sub], y=[r["y"] for r in sub],
                    mode="markers+text", name=cls,
                    text=[_html_text(r["name"], max_len=18) for r in sub],
                    textposition="top center", textfont=dict(size=9, color=_INK_2),
                    marker=dict(
                        size=[12 + 30 * (r["size"] / smax) for r in sub],
                        color=_PALETTE[ci % len(_PALETTE)], opacity=0.85,
                        line=dict(color=_SURFACE, width=2),
                    ),
                    hovertext=[r["hover"] for r in sub], hoverinfo="text",
                ))
            _apply_layout(fig, "Actors — Influence vs Salience "
                               "(bubble = relationship degree)", height=560)
            fig.update_layout(
                xaxis_title="Salience score",
                xaxis=dict(range=[-0.05, 1.1]),
                yaxis=dict(title="Influence", tickvals=[1, 2, 3, 4],
                           ticktext=["Low", "Medium", "High", "Very high"],
                           range=[0.4, 4.6]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "actor_influence_salience",
                                   "actor_influence_salience")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_actor_bubble_html 失败（跳过该图）：%s", exc)
            return None

    def build_source_sunburst_html(self, sources: Any, charts_dir: str) -> Optional[str]:
        """(D5) 来源构成 sunburst：tier（证据层级）→ source_origin（fetched/cited）→
        reachable（可达性）三层占比。"""
        if not self._interactive_ok():
            return None
        try:
            if not isinstance(sources, list) or not sources:
                return None
            counts: Dict[Tuple[str, str, str], int] = {}
            for s in sources:
                if not isinstance(s, dict):
                    continue
                tier = str(s.get("tier") or "untiered").strip() or "untiered"
                origin = str(s.get("source_origin") or "unknown").strip() or "unknown"
                reach = s.get("reachable")
                reach_lbl = ("reachable" if reach is True
                             else "unreachable" if reach is False else "unchecked")
                key = (tier, origin, reach_lbl)
                counts[key] = counts.get(key, 0) + 1
            if not counts:
                return None
            total = sum(counts.values())
            ids: List[str] = []
            labels: List[str] = []
            parents: List[str] = []
            values: List[int] = []
            colors: List[str] = []
            tier_totals: Dict[str, int] = {}
            origin_totals: Dict[Tuple[str, str], int] = {}
            for (tier, origin, _reach), n in counts.items():
                tier_totals[tier] = tier_totals.get(tier, 0) + n
                origin_totals[(tier, origin)] = origin_totals.get((tier, origin), 0) + n
            for tier in sorted(tier_totals, key=lambda t: (_TIER_RANK.get(t, 9), t)):
                ids.append(tier)
                labels.append(tier)
                parents.append("")
                values.append(tier_totals[tier])
                colors.append(_TIER_COLOR.get(tier, _COLOR_NEU))
            for (tier, origin) in sorted(origin_totals):
                ids.append(f"{tier}/{origin}")
                labels.append(origin)
                parents.append(tier)
                values.append(origin_totals[(tier, origin)])
                colors.append(_TIER_COLOR.get(tier, _COLOR_NEU))
            reach_color = {"reachable": _COLOR_GOOD, "unreachable": _COLOR_STALE,
                           "unchecked": _COLOR_NEU}
            for (tier, origin, reach) in sorted(counts):
                ids.append(f"{tier}/{origin}/{reach}")
                labels.append(reach)
                parents.append(f"{tier}/{origin}")
                values.append(counts[(tier, origin, reach)])
                colors.append(reach_color[reach])
            fig = go.Figure(go.Sunburst(
                ids=ids, labels=labels, parents=parents, values=values,
                branchvalues="total",
                marker=dict(colors=colors, line=dict(color=_SURFACE, width=1.5)),
                hovertemplate="%{id}: %{value} sources (%{percentRoot:.1%})<extra></extra>",
                textfont=dict(family=_VIZ_FONT, size=12),
            ))
            _apply_layout(fig, f"Source Mix — tier → origin → reachability "
                               f"(n={total})", height=560)
            return self._save_pair(fig, charts_dir, "source_mix_sunburst",
                                   "source_mix_sunburst")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_source_sunburst_html 失败（跳过该图）：%s", exc)
            return None

    def build_quantitative_dots_html(self, quantitative: Any,
                                     charts_dir: str) -> Optional[str]:
        """(D6) 量化断言点阵：按证据层级取 top ~30 行，按单位分组成子图（数值跨单位不可同轴），
        层级着色，陈旧（is_stale）行开圈红边标记。"""
        if not self._interactive_ok():
            return None
        try:
            if not isinstance(quantitative, list) or not quantitative:
                return None
            rows: List[Dict[str, Any]] = []
            for i, q in enumerate(quantitative):
                if not isinstance(q, dict):
                    continue
                val = _to_float(q.get("value"))
                metric = str(q.get("metric") or "").strip()
                unit = str(q.get("unit") or "").strip()
                if val is None or not metric or not unit or unit.lower() == "date":
                    continue
                tier = str(q.get("tier") or "S3").strip()
                rows.append({
                    "metric": metric, "value": val, "unit": unit, "tier": tier,
                    "stale": bool(q.get("is_stale")), "idx": i,
                    "hover": "<br>".join(x for x in (
                        f"<b>{_wrap_hover(metric, 56, max_len=160)}</b>",
                        f"{q.get('value')} {unit} · tier {tier}"
                        + (" · ⚠ stale" if q.get("is_stale") else ""),
                        f"as of {_html_text(q.get('as_of_date'), max_len=20)}"
                        if q.get("as_of_date") else "",
                        _wrap_hover(q.get("source"), 56, max_len=120),
                    ) if x),
                })
            if not rows:
                return None
            rows.sort(key=lambda r: (_TIER_RANK.get(r["tier"], 9), r["idx"]))
            rows = rows[:30]
            # 单位分组：行数最多的前 3 个单位（各 ≥2 行）各占一子图。
            unit_counts: Dict[str, int] = {}
            for r in rows:
                unit_counts[r["unit"]] = unit_counts.get(r["unit"], 0) + 1
            units = [u for u, c in sorted(unit_counts.items(),
                                          key=lambda kv: (-kv[1], kv[0])) if c >= 2][:3]
            if not units:
                return None
            from plotly.subplots import make_subplots
            groups = {u: [r for r in rows if r["unit"] == u] for u in units}
            heights = [len(groups[u]) for u in units]
            fig = make_subplots(
                rows=len(units), cols=1, shared_xaxes=False,
                subplot_titles=[f"{u} (n={len(groups[u])})" for u in units],
                row_heights=[max(h, 2) for h in heights], vertical_spacing=0.10,
            )
            shown_tiers: set = set()
            for ri, u in enumerate(units, 1):
                grp = groups[u]
                for tier in sorted({r["tier"] for r in grp},
                                   key=lambda t: (_TIER_RANK.get(t, 9), t)):
                    sub = [r for r in grp if r["tier"] == tier]
                    fig.add_trace(go.Scatter(
                        x=[r["value"] for r in sub],
                        y=[_html_text(r["metric"], max_len=46)
                           + (" ⚠" if r["stale"] else "") for r in sub],
                        mode="markers", name=f"tier {tier}", legendgroup=tier,
                        showlegend=tier not in shown_tiers,
                        marker=dict(
                            size=10,
                            color=[_SURFACE if r["stale"]
                                   else _TIER_COLOR.get(tier, _COLOR_NEU) for r in sub],
                            line=dict(
                                color=[_COLOR_STALE if r["stale"]
                                       else _TIER_COLOR.get(tier, _COLOR_NEU)
                                       for r in sub],
                                width=2),
                        ),
                        hovertext=[r["hover"] for r in sub], hoverinfo="text",
                    ), row=ri, col=1)
                    shown_tiers.add(tier)
            total_rows = sum(heights)
            _apply_layout(fig, "Key Quantitative Claims (top by evidence tier; "
                               "open red = stale)",
                          height=max(420, 26 * total_rows + 100 * len(units) + 120))
            fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                          xanchor="right", x=1))
            fig.update_yaxes(autorange="reversed")
            return self._save_pair(fig, charts_dir, "quantitative_claims",
                                   "quantitative_claims")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_quantitative_dots_html 失败（跳过该图）：%s", exc)
            return None

    def build_driver_tornado_html(self, ensemble: Any, charts_dir: str,
                                  graph_priors: Any = None) -> Optional[str]:
        """(D7) 驱动因子 tornado：把 ensemble 各情景的 key_drivers 按概率加权累计（近重词元
        合并），graph_priors 里命中的实体权重作乘性加成，取 top 15 排序横条。"""
        if not self._interactive_ok():
            return None
        try:
            scenarios = (ensemble.get("scenarios")
                         if isinstance(ensemble, dict) else ensemble)
            if not isinstance(scenarios, list) or not scenarios:
                return None
            n_sc = max(1, len(scenarios))
            canon: List[Dict[str, Any]] = []
            for sc in scenarios:
                if not isinstance(sc, dict):
                    continue
                w = _first_float(sc, ("mean_probability", "probability", "support_ratio"))
                if w is None:
                    w = 1.0 / n_sc
                sc_name = _html_text(sc.get("name"), fallback="scenario", max_len=40)
                drivers = sc.get("key_drivers")
                if not isinstance(drivers, list):
                    continue
                for d in drivers:
                    text = str(d or "").strip()
                    if not text:
                        continue
                    toks = _tokens(text)
                    hit = None
                    for c in canon:
                        if _jaccard(toks, c["tokens"]) >= 0.6:
                            hit = c
                            break
                    if hit is None:
                        hit = {"text": text, "tokens": toks, "weight": 0.0,
                               "scenarios": []}
                        canon.append(hit)
                    hit["weight"] += w
                    if sc_name not in hit["scenarios"]:
                        hit["scenarios"].append(sc_name)
            if not canon:
                return None
            priors: Dict[str, float] = {}
            if isinstance(graph_priors, dict):
                for k, v in graph_priors.items():
                    fv = _to_float(v)
                    if fv is not None and len(str(k).strip()) >= 3:
                        priors[str(k).strip()] = fv
            for c in canon:
                boost = 0.0
                boost_ent = ""
                low = c["text"].casefold()
                for ent, pv in priors.items():
                    if ent.casefold() in low and pv > boost:
                        boost = pv
                        boost_ent = ent
                c["score"] = c["weight"] * (1.0 + boost)
                c["boost_ent"] = boost_ent
            canon.sort(key=lambda c: (-c["score"], c["text"]))
            top = canon[:15]
            fig = go.Figure(go.Bar(
                x=[c["score"] for c in top],
                y=[_html_text(c["text"], max_len=64) for c in top],
                orientation="h", marker_color=_COLOR_MODEL,
                hovertext=["<br>".join(x for x in (
                    f"<b>{_wrap_hover(c['text'], 60)}</b>",
                    f"weighted salience {c['score']:.2f} "
                    f"(cited by {len(c['scenarios'])} scenario"
                    f"{'s' if len(c['scenarios']) != 1 else ''})",
                    f"graph-prior boost via {c['boost_ent']}" if c["boost_ent"] else "",
                ) if x) for c in top],
                hoverinfo="text",
            ))
            _apply_layout(fig, "Key Drivers — probability-weighted salience "
                               "across ensemble scenarios",
                          height=max(360, 34 * len(top) + 160))
            fig.update_layout(
                xaxis_title="Weighted driver salience (Σ scenario prob × prior boost)",
                yaxis=dict(autorange="reversed"),
            )
            return self._save_pair(fig, charts_dir, "driver_tornado", "driver_tornado")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_driver_tornado_html 失败（跳过该图）：%s", exc)
            return None

    def build_contested_dumbbell_html(self, contested: Any,
                                      charts_dir: str) -> Optional[str]:
        """(D8) 争议断言哑铃：每条争议断言取前两个对立立场，端点=证据权重（来源数 × 层级权重），
        差距即证据不对称度。按总权重取 top 12。"""
        if not self._interactive_ok():
            return None
        try:
            if not isinstance(contested, list) or not contested:
                return None
            rows: List[Dict[str, Any]] = []
            for c in contested:
                if not isinstance(c, dict):
                    continue
                claim = str(c.get("claim") or "").strip()
                positions = c.get("positions")
                if not claim or not isinstance(positions, list) or len(positions) < 2:
                    continue
                def _pw(pos: Any) -> Optional[Dict[str, Any]]:
                    if not isinstance(pos, dict):
                        return None
                    srcs = pos.get("sources")
                    n = len(srcs) if isinstance(srcs, list) else 1
                    tier = str(pos.get("tier") or "S3").strip()
                    return {
                        "w": max(0.5, n) * _TIER_WEIGHT.get(tier, 1.0),
                        "stance": str(pos.get("stance") or "").strip(),
                        "tier": tier, "n": n,
                    }
                a = _pw(positions[0])
                b = _pw(positions[1])
                if a is None or b is None:
                    continue
                rows.append({"claim": claim, "a": a, "b": b,
                             "total": a["w"] + b["w"]})
            if not rows:
                return None
            rows.sort(key=lambda r: (-r["total"], r["claim"]))
            rows = rows[:12]
            labels = [_html_text(r["claim"], max_len=58) for r in rows]
            fig = go.Figure()
            for lab, r in zip(labels, rows):
                fig.add_trace(go.Scatter(
                    x=[r["a"]["w"], r["b"]["w"]], y=[lab, lab], mode="lines",
                    line=dict(color=_COLOR_NEU, width=2),
                    showlegend=False, hoverinfo="skip",
                ))
            def _pos_hover(r: Dict[str, Any], key: str) -> str:
                p = r[key]
                return "<br>".join(x for x in (
                    f"<b>{_wrap_hover(r['claim'], 56, max_len=160)}</b>",
                    _wrap_hover(p["stance"], 60, max_len=280),
                    f"{p['n']} source{'s' if p['n'] != 1 else ''} · tier {p['tier']} "
                    f"→ weight {p['w']:.1f}",
                ) if x)
            fig.add_trace(go.Scatter(
                x=[r["a"]["w"] for r in rows], y=labels, mode="markers",
                name="Position A (first stance)",
                marker=dict(color=_COLOR_MODEL, size=11,
                            line=dict(color=_SURFACE, width=2)),
                hovertext=[_pos_hover(r, "a") for r in rows], hoverinfo="text",
            ))
            fig.add_trace(go.Scatter(
                x=[r["b"]["w"] for r in rows], y=labels, mode="markers",
                name="Position B (second stance)",
                marker=dict(color="#eb6834", size=11,
                            line=dict(color=_SURFACE, width=2)),
                hovertext=[_pos_hover(r, "b") for r in rows], hoverinfo="text",
            ))
            _apply_layout(fig, "Contested Claims — evidence weight per position",
                          height=max(360, 42 * len(rows) + 170))
            fig.update_layout(
                xaxis_title="Evidence weight (source count × tier weight)",
                yaxis=dict(autorange="reversed"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            return self._save_pair(fig, charts_dir, "contested_claims",
                                   "contested_claims")
        except Exception as exc:  # noqa: BLE001
            logger.debug("build_contested_dumbbell_html 失败（跳过该图）：%s", exc)
            return None

    # ============================ 编排入口 ============================

    def build_all(self, report_id: str, report_dir: str,
                  artifacts: Dict[str, Any]) -> List[Dict[str, str]]:
        """WAVE9 plotly-first 编排：把所有可用工件渲染成 HTML+PNG 图表对，落盘 charts/，
        持久化 viz_manifest.json（{"schema_version":2,"items":[...],"skipped":[...]}），
        返回 items 列表（签名不变）。

        artifacts 键（全部可选，缺失即记 skipped['no_input'] 并跳过对应图）：
          forecast              forecast.json（dict）—— scenarios / binary_forecasts
          ensemble              ensemble_forecast.json（dict）—— 情景误差带 + 驱动因子 tornado
          timeline              timeline.json（list 或 {timeline:[...]}）
          actors                actors.json（dict：actors[] + relationships[]）
          quantitative          quantitative.json（list of rows）
          sources               sources.json（list）
          contested             contested.json（list）
          graph_priors          graph_priors.json（{entity: weight}）
          graph_priors_structural  结构先验（graph_priors 缺失时的回退）
          world_state_trajectory  world_state_trajectory.json（dict）
          comparison            comparison.json（dict，matplotlib 专属）
          calibration           校准统计（matplotlib 专属）
          market_price_history  {market_id:[{t,p}]}（缺则回退读 reports/{id}/ 与 handoff/ 同名文件）
          handoff_dir           研究 handoff 目录（定位 handoff/market_price_history.json）

        manifest 每项：{id,path,type,title,caption,source,placement_hint}
          + 可选 png_path（交互式 HTML 图的 kaleido 静态 PNG 对，供 PDF/exec-brief 内嵌）。
        caption 与 title 同值（caption 为旧消费方兼容别名）。

        降级链：plotly 缺失/关闭 → 整族记 skipped 并回退 matplotlib PNG 族；kaleido 缺失/
        运行失败 → HTML 保留、PNG 经 matplotlib 等价图补齐（有等价图时）。总开关
        REPORT_VISUALIZER 关闭 → 返回 [] 且不落盘（degrade-safe）。"""
        items: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []
        if not bool(_cfg("REPORT_VISUALIZER", True)):
            return items
        artifacts = artifacts or {}
        charts_dir = os.path.join(report_dir, "charts")

        # WAVE9：PNG 批处理状态复位（一次 build_all 一批，末尾统一 flush）。
        self._png_jobs = []
        self._png_done = {}
        self._batch_mode = True
        self._kaleido_failed = False

        forecast = artifacts.get("forecast")
        ensemble = artifacts.get("ensemble")
        actors = artifacts.get("actors")
        graph_priors = artifacts.get("graph_priors")
        if not isinstance(graph_priors, dict) or not graph_priors:
            graph_priors = artifacts.get("graph_priors_structural")
        plotly_on = self._interactive_ok()

        def _attempt(item_id: str, source: str, title: str, hint: str,
                     data_ok: bool, fn) -> None:
            """跑一个 plotly 构建器并登记结果/跳过原因（绝不抛）。"""
            if not plotly_on:
                skipped.append({"builder": item_id, "reason": "plotly_unavailable_or_disabled"})
                return
            if not data_ok:
                skipped.append({"builder": item_id, "reason": "no_input"})
                return
            try:
                rel = fn()
            except Exception as exc:  # noqa: BLE001 - 构建器自身已兜底，这里双保险
                logger.debug("可视化构建器 %s 异常：%s", item_id, exc)
                skipped.append({"builder": item_id,
                                "reason": f"exception:{type(exc).__name__}"})
                return
            if not rel:
                skipped.append({"builder": item_id, "reason": "empty_after_parse"})
                return
            items.append({
                "id": item_id, "path": rel, "type": "html",
                "title": title, "caption": title,
                "source": source, "placement_hint": hint,
            })

        try:
            # ---- (C/D) plotly 主族 ----
            scen_ok = bool(_scenario_data_present(forecast, ensemble))
            _attempt("scenario_probabilities", "forecast",
                     "Scenario Probabilities (ensemble spread)", "scenarios",
                     scen_ok,
                     lambda: self.build_scenario_bars_html(forecast, charts_dir,
                                                           ensemble=ensemble))
            bfs = forecast.get("binary_forecasts") if isinstance(forecast, dict) else None
            _attempt("binary_forecast_dotplot", "forecast",
                     "Binary Forecasts — P(yes) with confidence", "binary_forecasts",
                     isinstance(bfs, list) and bool(bfs),
                     lambda: self.build_binary_dotplot_html(forecast, charts_dir))
            _attempt("model_vs_market", "forecast",
                     "Model vs Market (binary forecasts)", "binary_forecasts",
                     isinstance(bfs, list) and bool(bfs),
                     lambda: self.build_model_vs_market_html(forecast, charts_dir))
            tl = artifacts.get("timeline")
            _attempt("timeline_lanes", "timeline", "Event Timeline", "timeline",
                     bool(tl),
                     lambda: self.build_timeline_lanes_html(tl, charts_dir))
            _attempt("actor_network", "actors", "Actor Relationship Network", "actors",
                     bool(actors),
                     lambda: self.build_actor_network_html(actors, charts_dir,
                                                           graph_priors=graph_priors))
            _attempt("actor_influence_salience", "actors",
                     "Actors — Influence vs Salience", "actors",
                     bool(actors),
                     lambda: self.build_actor_bubble_html(actors, charts_dir))
            src = artifacts.get("sources")
            _attempt("source_mix_sunburst", "sources",
                     "Source Mix — tier / origin / reachability", "sources",
                     isinstance(src, list) and bool(src),
                     lambda: self.build_source_sunburst_html(src, charts_dir))
            quant = artifacts.get("quantitative")
            _attempt("quantitative_claims", "quantitative",
                     "Key Quantitative Claims", "quantitative",
                     isinstance(quant, list) and bool(quant),
                     lambda: self.build_quantitative_dots_html(quant, charts_dir))
            _attempt("driver_tornado", "ensemble",
                     "Key Drivers — ensemble-weighted salience", "drivers",
                     bool(ensemble),
                     lambda: self.build_driver_tornado_html(ensemble, charts_dir,
                                                            graph_priors=graph_priors))
            cont = artifacts.get("contested")
            _attempt("contested_claims", "contested",
                     "Contested Claims — evidence weight per position", "contested",
                     isinstance(cont, list) and bool(cont),
                     lambda: self.build_contested_dumbbell_html(cont, charts_dir))
            wst = artifacts.get("world_state_trajectory")
            _attempt("worldstate_trajectory", "world_state_trajectory",
                     "Modeled Outcome-Share Trajectory", "simulation",
                     bool(wst),
                     lambda: self.build_worldstate_area_html(wst, charts_dir))

            # 市场价格历史折线（多图族，逐图登记）。
            if not self._price_hist_ok():
                skipped.append({"builder": "market_price_history", "reason": "disabled"})
            else:
                price_history = self._load_price_history(report_dir, artifacts)
                ph_anchors = (forecast.get("binary_forecasts")
                              if isinstance(forecast, dict) else None)
                if not (isinstance(price_history, dict) and price_history
                        and isinstance(ph_anchors, list) and ph_anchors):
                    skipped.append({"builder": "market_price_history", "reason": "no_input"})
                elif not plotly_on:
                    skipped.append({"builder": "market_price_history",
                                    "reason": "plotly_unavailable_or_disabled"})
                else:
                    ph_rels = self.render_market_price_history_html(
                        price_history, ph_anchors, charts_dir)
                    if not ph_rels:
                        skipped.append({"builder": "market_price_history",
                                        "reason": "empty_after_parse"})
                    for rel in ph_rels:
                        stem = os.path.splitext(os.path.basename(rel))[0]
                        items.append({
                            "id": stem, "path": rel, "type": "html",
                            "title": "Market-Implied P(yes) History vs Model",
                            "caption": "Market-Implied P(yes) History vs Model",
                            "source": "market_price_history",
                            "placement_hint": "binary_forecasts",
                        })
        finally:
            self._batch_mode = False

        # ---- kaleido PNG 批量导出 + png_path 挂载 ----
        self._flush_png_jobs()
        for it in items:
            png = self._png_done.get(it["id"])
            if png and os.path.exists(os.path.join(report_dir, png)):
                it["png_path"] = png

        # ---- (B) matplotlib 回退族：为缺 PNG 对的核心图补静态图（PDF/exec-brief 需要），
        # plotly 整族缺席时这些图自身就是 manifest 项。comparison/calibration 无 plotly 等价，
        # 始终由 matplotlib 生成。----
        self._run_matplotlib_family(report_dir, charts_dir, artifacts, forecast,
                                    ensemble, items, skipped)

        # ---- 落盘 manifest（原子写；失败不影响已生成的图表）+ INFO 汇总（不再沉默跳过）----
        self._persist_manifest(report_dir, items, skipped)
        skip_summary = ", ".join(f"{s['builder']}={s['reason']}" for s in skipped) or "-"
        logger.info("报告可视化完成 report=%s：产出 %d 项（png_pair=%d），跳过 %d 项 [%s]",
                    report_id, len(items),
                    sum(1 for it in items if it.get("png_path")),
                    len(skipped), skip_summary)
        return items

    def _run_matplotlib_family(self, report_dir: str, charts_dir: str,
                               artifacts: Dict[str, Any], forecast: Any,
                               ensemble: Any, items: List[Dict[str, str]],
                               skipped: List[Dict[str, str]]) -> None:
        """WAVE9 matplotlib 回退族：
          · scenario/model-vs-market/worldstate/价格历史——仅当对应 plotly 项缺失或缺 PNG 对时
            生成（kaleido 失败/关闭时补 PDF 可嵌静态图）；有 plotly 项则把 PNG 挂到 png_path，
            无则作为独立 png manifest 项。
          · comparison/calibration——无 plotly 等价图，数据在即生成。
        matplotlib 缺失/REPORT_VIZ_CHARTS 关闭 → 整族记 skipped。"""
        if not self._chart_ok():
            skipped.append({"builder": "matplotlib_family",
                            "reason": "matplotlib_unavailable_or_disabled"})
            return

        by_id = {it["id"]: it for it in items}

        def _fallback(item_id: str, source: str, title: str, hint: str, fn) -> None:
            existing = by_id.get(item_id)
            if existing is not None and existing.get("png_path"):
                return  # plotly PNG 对已就位，无需回退
            try:
                rel = fn()
            except Exception as exc:  # noqa: BLE001
                logger.debug("matplotlib 回退 %s 异常：%s", item_id, exc)
                return
            if not rel:
                return
            if existing is not None:
                existing["png_path"] = rel  # HTML 在、PNG 缺 → 挂载回退 PNG
            else:
                items.append({
                    "id": item_id, "path": rel, "type": "png",
                    "title": title, "caption": title,
                    "source": source, "placement_hint": hint,
                })

        _fallback("scenario_probabilities", "forecast",
                  "Scenario Probabilities", "scenarios",
                  lambda: self.build_scenario_bars(forecast, charts_dir,
                                                   ensemble=ensemble))
        _fallback("model_vs_market", "forecast", "Model vs Market",
                  "binary_forecasts",
                  lambda: self.build_model_vs_market(forecast, charts_dir))
        _fallback("worldstate_trajectory", "world_state_trajectory",
                  "Modeled Outcome-Share Trajectory", "simulation",
                  lambda: self.build_worldstate_area(
                      artifacts.get("world_state_trajectory"), charts_dir))

        # 价格历史回退：任何 plotly 价格历史项缺 PNG 时重画 matplotlib 版（同名 stem 覆盖挂载）。
        ph_items = [it for it in items if it["id"].startswith("market_price_history")]
        need_ph = ((not ph_items) or any(not it.get("png_path") for it in ph_items))
        if need_ph and self._price_hist_ok():
            price_history = self._load_price_history(report_dir, artifacts)
            ph_anchors = (forecast.get("binary_forecasts")
                          if isinstance(forecast, dict) else None)
            if isinstance(price_history, dict) and price_history \
                    and isinstance(ph_anchors, list) and ph_anchors:
                for rel in self.render_market_price_history(price_history, ph_anchors,
                                                            charts_dir):
                    stem = os.path.splitext(os.path.basename(rel))[0]
                    existing = by_id.get(stem)
                    if existing is not None:
                        if not existing.get("png_path"):
                            existing["png_path"] = rel
                    else:
                        items.append({
                            "id": stem, "path": rel, "type": "png",
                            "title": "Market-Implied P(yes) History vs Model",
                            "caption": "Market-Implied P(yes) History vs Model",
                            "source": "market_price_history",
                            "placement_hint": "binary_forecasts",
                        })

        # comparison / calibration：matplotlib 专属图（无 plotly 等价）。
        comp = artifacts.get("comparison")
        if comp:
            rel = self.build_comparison_bars(comp, charts_dir)
            if rel:
                items.append({
                    "id": "comparison_bars", "path": rel, "type": "png",
                    "title": "Baseline vs Scenario", "caption": "Baseline vs Scenario",
                    "source": "comparison", "placement_hint": "comparison",
                })
            else:
                skipped.append({"builder": "comparison_bars",
                                "reason": "empty_after_parse"})
        else:
            skipped.append({"builder": "comparison_bars", "reason": "no_input"})
        calib = artifacts.get("calibration")
        if calib:
            rel = self.build_calibration_curve(calib, charts_dir)
            if rel:
                items.append({
                    "id": "calibration_curve", "path": rel, "type": "png",
                    "title": "Calibration Curve", "caption": "Calibration Curve",
                    "source": "calibration", "placement_hint": "calibration",
                })
            else:
                skipped.append({"builder": "calibration_curve",
                                "reason": "empty_after_parse"})
        else:
            skipped.append({"builder": "calibration_curve", "reason": "no_input"})

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
    def _persist_manifest(report_dir: str, items: List[Dict[str, str]],
                          skipped: Optional[List[Dict[str, str]]] = None) -> None:
        """原子写 reports/{id}/viz_manifest.json（WAVE9 schema v2：
        {"schema_version":2,"items":[...],"skipped":[{builder,reason}]}）。
        目录不可写等失败仅 debug 日志（degrade-safe）。"""
        try:
            os.makedirs(report_dir, exist_ok=True)
            path = os.path.join(report_dir, "viz_manifest.json")
            tmp = path + ".tmp"
            payload = {
                "schema_version": 2,
                "items": items,
                "skipped": skipped or [],
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("viz_manifest.json 落盘失败：%s", exc)

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


# ─────────────────────────────────────────────────────────────────────────────
# WAVE9 模块级助手（纯函数）：情景行抽取、时间线整备、角色名归一、网络布局。
# ─────────────────────────────────────────────────────────────────────────────

def _extract_scenario_rows(forecast: Any, ensemble: Any = None) -> List[Dict[str, Any]]:
    """统一情景行抽取（matplotlib 与 plotly 情景柱共用，保证同一数据）：
    优先 ensemble_forecast.json 的 scenarios（mean_probability + min/max/stdev/support_ratio
    误差带），缺失/无效时回退 forecast.json scenarios（probability + p_low/p_high 等区间键）。
    返回 [{name,p,lo,hi,stdev,support}]（按 p 降序，上限 14 行）；无有效情景 → []。"""
    rows: List[Dict[str, Any]] = []
    try:
        ens_list = ensemble.get("scenarios") if isinstance(ensemble, dict) else None
        if isinstance(ens_list, list):
            for i, s in enumerate(ens_list, 1):
                if not isinstance(s, dict):
                    continue
                p = _first_float(s, ("mean_probability", "probability", "prob", "p"))
                if p is None:
                    continue
                lo = _first_float(s, ("min", "p_low", "prob_low", "ci_low", "low"))
                hi = _first_float(s, ("max", "p_high", "prob_high", "ci_high", "high"))
                if lo is None or hi is None or not (lo <= p <= hi):
                    lo, hi = None, None
                rows.append({
                    "name": _html_text(s.get("name") or s.get("label"),
                                       fallback=f"Scenario {i}", max_len=56),
                    "p": p, "lo": lo, "hi": hi,
                    "stdev": _first_float(s, ("stdev", "std")),
                    "support": _first_float(s, ("support_ratio",)),
                })
        if not rows:
            scenarios = forecast.get("scenarios") if isinstance(forecast, dict) else forecast
            if isinstance(scenarios, list):
                for i, s in enumerate(scenarios, 1):
                    if not isinstance(s, dict):
                        continue
                    p = _first_float(s, ("probability", "prob", "p"))
                    if p is None:
                        continue
                    lo = _first_float(s, ("p_low", "prob_low", "ci_low", "low"))
                    hi = _first_float(s, ("p_high", "prob_high", "ci_high", "high"))
                    if lo is None or hi is None or not (lo <= p <= hi):
                        lo, hi = None, None
                    rows.append({
                        "name": _html_text(s.get("name") or s.get("label"),
                                           fallback=f"Scenario {i}", max_len=56),
                        "p": p, "lo": lo, "hi": hi, "stdev": None, "support": None,
                    })
        rows.sort(key=lambda r: (-r["p"], r["name"]))
        return rows[:14]
    except Exception:  # noqa: BLE001
        return []


def _scenario_data_present(forecast: Any, ensemble: Any) -> bool:
    """build_all 的输入在位判定（区分 no_input 与 empty_after_parse）。"""
    if isinstance(ensemble, dict) and isinstance(ensemble.get("scenarios"), list) \
            and ensemble.get("scenarios"):
        return True
    if isinstance(forecast, dict) and isinstance(forecast.get("scenarios"), list) \
            and forecast.get("scenarios"):
        return True
    return isinstance(forecast, list) and bool(forecast)


_FLEX_DATE_RE = re.compile(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")


def _parse_flex_date(s: Any):
    """宽松日期解析：'2026'/'2026-03'/'2026-03-15' → datetime（缺月取 6 月、缺日取 15 日，
    保证同年/同月事件落在区间中部）。无法解析 → None。"""
    import datetime as _dt
    m = _FLEX_DATE_RE.search(str(s or ""))
    if not m:
        return None
    try:
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else 6
        day = int(m.group(3)) if m.group(3) else 15
        if not (1900 <= year <= 2200 and 1 <= month <= 12):
            return None
        day = min(max(day, 1), 28)  # 钳到 28 避免月长判断
        return _dt.datetime(year, month, day)
    except (TypeError, ValueError):
        return None


# 时间线泳道分类（顺序即优先级，首个命中生效；未命中 → 'Other'）。
_TL_CATEGORIES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Policy & Export Controls",
     ("entity list", "export control", "export-control", "export restriction", "bis",
      "chips act", "tariff", "sanction", "subsid", "regulation", "license", "ban",
      "act signed", "executive order", "match act", "commerce", "waiver")),
    ("Geopolitics",
     ("taiwan strait", "invasion", "blockade", "war", "military", "ceasefire",
      "geopolit", "election", "president", "minister")),
    ("Markets & Finance",
     ("revenue", "capex", "market cap", "stock", "ipo", "bankrupt", "chapter 11",
      "funding", "billion", "trillion", "guidance", "forecast", "earnings",
      "acquisition", "merger", "investment")),
    ("Companies & Technology",
     ("fab", "nm", "yield", "hbm", "euv", "launch", "tape-out", "tapeout", "node",
      "packag", "cowos", "chip", "gpu", "foundry", "memory", "wafer", "announce",
      "production", "capacity")),
]


def _tl_category(text: str) -> str:
    low = text.lower()
    for cat, kws in _TL_CATEGORIES:
        if any(kw in low for kw in kws):
            return cat
    return "Other"


def _prepare_timeline_events(rows: List[Any]) -> List[Dict[str, Any]]:
    """时间线事件整备：键名兼容（date/日期/when，event/事件/text/label/description）、
    宽松日期解析（不可解析日期的行剔除）、同日高词元重合去重（Jaccard ≥ 0.55，保留首现）、
    显著度评分（含数字/货币符号 + 文本长度），超上限（REPORT_VIZ_TIMELINE_MAX_EVENTS，
    默认 40）按显著度截断，最终按时间升序。"""
    events: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        date_raw = str(r.get("date") or r.get("日期") or r.get("when") or "").strip()
        text = str(r.get("event") or r.get("事件") or r.get("text")
                   or r.get("label") or r.get("description") or "").strip()
        if not text:
            continue
        dt = _parse_flex_date(date_raw)
        if dt is None:
            continue
        toks = _tokens(text)
        # 同日近重去重（诊断实锤：同日 CHIPS Act / 2029-01-01 各出现两次）。
        dup = False
        for e in events:
            if e["dt"] == dt and _jaccard(toks, e["tokens"]) >= 0.55:
                dup = True
                break
        if dup:
            continue
        score = (1.0 if re.search(r"[$%€¥]|\d", text) else 0.0) + min(len(text) / 160.0, 1.0)
        events.append({"date": date_raw or dt.strftime("%Y-%m-%d"), "dt": dt,
                       "text": text, "tokens": toks, "score": score,
                       "cat": _tl_category(text)})
    if not events:
        return []
    cap = int(_cfg("REPORT_VIZ_TIMELINE_MAX_EVENTS", 40) or 40)
    if cap > 0 and len(events) > cap:
        events = sorted(events, key=lambda e: -e["score"])[:cap]
    events.sort(key=lambda e: (e["dt"], e["text"]))
    return events


def _norm_key(name: Any) -> str:
    """实体名归一键：casefold + 折叠空白 + 去首尾标点。"""
    return re.sub(r"\s+", " ", str(name or "")).strip(" .,;:'\"()").casefold()


def _canonical_actor_map(actor_list: Any) -> Dict[str, str]:
    """从 actors[]（name + aliases）构建 归一键 → 规范名 映射，用于把关系端点上的
    'Samsung'/'Samsung Electronics' 类重复实体合并到同一节点。确定性：按名字排序遍历。"""
    canon: Dict[str, str] = {}
    names: List[str] = []
    if isinstance(actor_list, list):
        for a in actor_list:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            canon[_norm_key(name)] = name
            aliases = a.get("aliases")
            if isinstance(aliases, list):
                for al in aliases:
                    k = _norm_key(al)
                    if k and k not in canon:
                        canon[k] = name
    return canon


def _canonicalize(name: str, canon: Dict[str, str]) -> str:
    """把端点名映射到规范名：精确（含 alias）→ 前缀/包含（≥4 字符，唯一命中才归并，
    多个候选取最长规范名保证确定性）→ 原名。"""
    if not name:
        return ""
    key = _norm_key(name)
    if key in canon:
        return canon[key]
    if len(key) >= 4:
        hits = []
        for k, v in canon.items():
            if (key in k or k in key) and min(len(key), len(k)) >= 4:
                hits.append(v)
        if hits:
            return sorted(set(hits), key=lambda v: (-len(v), v))[0]
    return name


def _network_layout(nodes: List[str],
                    edges: List[Tuple[str, str, str, str]]) -> Dict[str, Tuple[float, float]]:
    """网络布局：networkx spring（固定 seed=7，k=0.9/√n，确定性）；networkx 缺失 → 按度数
    降序的确定性同心圆回退（高度数在内圈）。"""
    if NETWORKX_AVAILABLE:
        try:
            g = nx.Graph()
            g.add_nodes_from(nodes)
            g.add_edges_from([(s, t) for s, t, _typ, _sg in edges])
            n = max(1, g.number_of_nodes())
            pos = nx.spring_layout(g, seed=7, k=0.9 / math.sqrt(n), iterations=60)
            return {str(k): (float(v[0]), float(v[1])) for k, v in pos.items()}
        except Exception as exc:  # noqa: BLE001
            logger.debug("networkx spring 布局失败，回退同心圆：%s", exc)
    # 确定性同心圆回退：度数高者在内圈。
    degree: Dict[str, int] = dict.fromkeys(nodes, 0)
    for s, t, _typ, _sg in edges:
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    ordered = sorted(nodes, key=lambda n: (-degree.get(n, 0), n))
    pos: Dict[str, Tuple[float, float]] = {}
    ring = 0
    idx = 0
    ring_cap = 6
    for name in ordered:
        radius = 0.35 + 0.55 * ring
        theta = 2 * math.pi * idx / ring_cap
        pos[name] = (radius * math.cos(theta), radius * math.sin(theta))
        idx += 1
        if idx >= ring_cap:
            ring += 1
            idx = 0
            ring_cap = int(ring_cap * 1.8)
    return pos
