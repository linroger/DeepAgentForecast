"""DRF-2 KG 引擎 —— MCP 工具实现层（纯封装 legacy 图谱栈，不重实现图逻辑）。

封装对象（对应 REDESIGN.md §2「KG engine」）：
- ``app.services.zep_tools.ZepToolsService``          语义检索 / 节点边读取 / trace_cascade
- ``app.services.graphiti_client.runtime``            add_episode / causal_paths / n_hop_subgraph
- ``app.services.graph_builder.GraphBuilderService``  中心度 / 介数 / 咽喉先验（kg_centrality_priors）

契约：
1. 所有 legacy 导入均为惰性——首次 tools/call 才加载 Graphiti/FalkorDB/嵌入模型；
   MCP 服务器启动与 tools/list 完全离线、零重依赖（deferred tool_search 友好）。
2. 每个工具函数返回 ``str``（to_text 文本或 JSON 字符串）；异常一律折叠为错误文本
   （degrade-safe：单次工具失败绝不打崩 stdio 服务器进程）。
3. ``graph_id`` 可逐调用传入，也可由 ``server --graph-id`` 设为进程默认；显式传入优先。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "backend"

# 进程级默认 graph_id（server --graph-id 注入；工具参数显式传入时优先）。
_DEFAULT_GRAPH_ID = ""

# 懒加载单例注册表（zep_tools / runtime / graph_builder）；加锁防并发双初始化。
_services: Dict[str, Any] = {}
_services_lock = threading.RLock()

_MISSING_GRAPH_MSG = (
    "参数错误：graph_id 缺失。请在调用参数里传 graph_id，"
    "或以 --graph-id 启动 KG 引擎设定进程默认图谱。"
)


def set_default_graph_id(graph_id: str) -> None:
    """设定进程默认图谱 ID（server 启动参数注入）。"""
    global _DEFAULT_GRAPH_ID
    _DEFAULT_GRAPH_ID = (graph_id or "").strip()


def _resolve_graph_id(graph_id: str) -> str:
    return (graph_id or "").strip() or _DEFAULT_GRAPH_ID


def _ensure_backend_importable() -> None:
    """把 legacy backend 根目录放进 sys.path，使 ``import app.…`` 可用。

    与 backend/scripts/run_parallel_simulation.py 的自举方式一致；只加 path，
    不修改任何 legacy 代码。"""
    p = str(_BACKEND_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _zep_tools():
    """ZepToolsService 懒加载单例（检索/读取工具面）。"""
    with _services_lock:
        svc = _services.get("zep_tools")
        if svc is None:
            _ensure_backend_importable()
            from app.services.zep_tools import ZepToolsService

            svc = ZepToolsService()
            _services["zep_tools"] = svc
        return svc


def _graphiti_runtime():
    """GraphitiRuntime 懒加载单例（写入 + 多跳遍历原语）。"""
    with _services_lock:
        rt = _services.get("runtime")
        if rt is None:
            _ensure_backend_importable()
            from app.services.graphiti_client.runtime import get_runtime

            rt = get_runtime()
            _services["runtime"] = rt
        return rt


def _graph_builder():
    """GraphBuilderService 懒加载单例（结构先验计算）。"""
    with _services_lock:
        gb = _services.get("graph_builder")
        if gb is None:
            _ensure_backend_importable()
            from app.services.graph_builder import GraphBuilderService

            gb = GraphBuilderService()
            _services["graph_builder"] = gb
        return gb


def _err(tool: str, exc: Exception) -> str:
    """degrade-safe 错误面：折叠成一行错误文本，绝不向 MCP 层抛异常。"""
    return f"KG engine error [{tool}]: {type(exc).__name__}: {exc}"


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _clamp(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(v, hi))


def _causal_edge_names(svc) -> list | None:
    """从 ZepToolsService 读因果族边名清单（单一事实来源，避免此处复制清单造成漂移）。"""
    names = getattr(type(svc), "_CAUSAL_EDGE_NAMES", None)
    return list(names) if names else None


def _resolve_entity(svc, graph_id: str, name: str) -> str:
    """best-effort 把传入实体名解析为图谱规范节点名（复用 legacy R2-KG-2 解析器）。
    解析失败按原名走（degrade-safe），与 trace_cascade 的行为一致。"""
    try:
        return svc._resolve_entity_name(graph_id, name)  # noqa: SLF001 — 复用而非重实现
    except Exception:  # noqa: BLE001
        return name


def _invalidate_read_caches(graph_id: str) -> None:
    """写图后使已建 ZepToolsService 单例的节点/边/检索缓存失效（避免读到陈旧快照）。
    单例尚未构建则无缓存可失效；任何失败都吞掉（缓存失效是增强项，绝不阻断写入结果）。"""
    svc = _services.get("zep_tools")
    if svc is None:
        return
    try:
        svc.invalidate_search_cache(graph_id)
        # 节点/边缓存无公开失效入口（legacy 仅在 interview 写图路径内部处理），
        # 此处 best-effort 触达私有缓存；cutover 时应在 legacy 提为公开 API。
        with svc._cache_lock:  # noqa: SLF001
            svc._nodes_cache.pop(graph_id, None)  # noqa: SLF001
            svc._edges_cache.pop((graph_id, True), None)  # noqa: SLF001
            svc._edges_cache.pop((graph_id, False), None)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════ MCP 工具实现 ══════════════════════════════


def kg_add_episode(
    body: str,
    name: str = "episode",
    graph_id: str = "",
    source_description: str = "",
    reference_time: str = "",
) -> str:
    """写入一条文本 episode（Graphiti 自动抽取实体/关系入图）。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    if not (body or "").strip():
        return "参数错误：body 不能为空（需要待入图的文本内容）。"
    try:
        ref_dt = None
        ref_note = ""
        raw_ref = (reference_time or "").strip()
        if raw_ref:
            _ensure_backend_importable()
            from app.utils.dates import parse_as_of  # 宽松日期解析（ISO/斜杠/中文年月日）

            ref_dt = parse_as_of(raw_ref)
            if ref_dt is None:
                ref_note = f"（reference_time {raw_ref!r} 无法解析，已按无时间戳写入）"
        rt = _graphiti_runtime()
        episode_uuid = rt.add_episode(
            gid,
            name=(name or "episode").strip() or "episode",
            body=body,
            source_type="text",
            source_description=(source_description or "").strip() or "drf2-kg-mcp",
            reference_time=ref_dt,
        )
        _invalidate_read_caches(gid)
        return f"episode 已写入图谱 {gid}: uuid={episode_uuid}{ref_note}"
    except Exception as exc:  # noqa: BLE001 — degrade-safe 错误面
        return _err("kg_add_episode", exc)


def kg_search(
    query: str,
    graph_id: str = "",
    limit: int = 10,
    scope: str = "edges",
    as_of: str = "",
) -> str:
    """混合语义+BM25 检索（cross-encoder 重排），可选 as-of 时点过滤。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    if not (query or "").strip():
        return "参数错误：query 不能为空。"
    scope_norm = (scope or "edges").strip().lower()
    if scope_norm not in ("edges", "nodes"):
        scope_norm = "edges"
    try:
        svc = _zep_tools()
        # as_of_search 内部宽松解析时点；as_of 为空/不可解析时退化为全时段 search_graph，
        # 两条路径都走 legacy 的语义检索 + cross-encoder 重排 + 本地关键词降级兜底。
        result = svc.as_of_search(
            gid,
            (query or "").strip(),
            (as_of or "").strip() or None,
            limit=_clamp(limit, 1, 50, 10),
            scope=scope_norm,
        )
        return result.to_text()
    except Exception as exc:  # noqa: BLE001
        return _err("kg_search", exc)


def kg_get_entities(graph_id: str = "", entity_type: str = "", limit: int = 50) -> str:
    """列出图谱实体（节点），可按本体实体类型过滤。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    try:
        svc = _zep_tools()
        etype = (entity_type or "").strip()
        nodes = svc.get_entities_by_type(gid, etype) if etype else svc.get_all_nodes(gid)
        lim = _clamp(limit, 1, 500, 50)
        header = f"图谱 {gid} 共 {len(nodes)} 个实体"
        if etype:
            header += f"（类型={etype}）"
        if len(nodes) > lim:
            header += f"，显示前 {lim} 个"
        lines = [header]
        for node in nodes[:lim]:
            lines.append(f"- {node.to_text()} [uuid={node.uuid}]")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err("kg_get_entities", exc)


def kg_get_edges(
    graph_id: str = "",
    node_uuid: str = "",
    include_temporal: bool = True,
    limit: int = 50,
) -> str:
    """列出关系边（含事实与时效 valid_at/invalid_at），可限定到单个节点的边。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    try:
        svc = _zep_tools()
        nuid = (node_uuid or "").strip()
        if nuid:
            edges = svc.get_node_edges(gid, nuid)
            header = f"节点 {nuid} 相关边共 {len(edges)} 条"
        else:
            edges = svc.get_all_edges(gid, include_temporal=include_temporal)
            header = f"图谱 {gid} 共 {len(edges)} 条边"
        lim = _clamp(limit, 1, 500, 50)
        if len(edges) > lim:
            header += f"，显示前 {lim} 条"
        lines = [header]
        for edge in edges[:lim]:
            lines.append(f"- {edge.to_text(include_temporal=include_temporal)} [uuid={edge.uuid}]")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return _err("kg_get_edges", exc)


def kg_causal_paths(
    source: str,
    target: str,
    graph_id: str = "",
    max_hops: int = 4,
    limit: int = 20,
    causal_only: bool = True,
    as_of: str = "",
) -> str:
    """source→target 的有向多跳因果路径（逐跳 sign/strength/lag + 净极性），JSON 输出。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    if not (source or "").strip() or not (target or "").strip():
        return "参数错误：source 与 target 均不能为空。"
    try:
        svc = _zep_tools()
        rt = _graphiti_runtime()
        rsource = _resolve_entity(svc, gid, source.strip())
        rtarget = _resolve_entity(svc, gid, target.strip())
        edge_types = _causal_edge_names(svc) if causal_only else None
        kwargs = dict(
            max_hops=_clamp(max_hops, 1, 6, 4),
            limit=_clamp(limit, 1, 100, 20),
            as_of=(as_of or "").strip() or None,
        )
        paths = rt.causal_paths(gid, rsource, rtarget, edge_types=edge_types, **kwargs) or []
        note = ""
        if not paths and causal_only and edge_types:
            # 与 legacy trace_cascade 同语义：无纯因果路径时放宽到一般关系路径并显式标注。
            paths = rt.causal_paths(gid, rsource, rtarget, edge_types=None, **kwargs) or []
            if paths:
                note = "无纯因果路径，以下为一般关系路径"
        payload = {
            "graph_id": gid,
            "source": rsource,
            "target": rtarget,
            "causal_only": bool(causal_only),
            "path_count": len(paths),
            "paths": paths,
        }
        if note:
            payload["note"] = note
        if not paths:
            payload["note"] = f"{rsource} → {rtarget} 间未找到 ≤{kwargs['max_hops']} 跳路径"
        return _json(payload)
    except Exception as exc:  # noqa: BLE001
        return _err("kg_causal_paths", exc)


def kg_n_hop_subgraph(
    center: str,
    graph_id: str = "",
    max_hops: int = 2,
    causal_only: bool = False,
    limit: int = 60,
    as_of: str = "",
) -> str:
    """以 center 为中心的 N 跳邻域边集（含逐边因果属性），JSON 输出。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    if not (center or "").strip():
        return "参数错误：center 不能为空。"
    try:
        svc = _zep_tools()
        rt = _graphiti_runtime()
        rcenter = _resolve_entity(svc, gid, center.strip())
        edge_types = _causal_edge_names(svc) if causal_only else None
        edges = rt.n_hop_subgraph(
            gid,
            rcenter,
            max_hops=_clamp(max_hops, 1, 4, 2),
            edge_types=edge_types,
            limit=_clamp(limit, 1, 300, 60),
            as_of=(as_of or "").strip() or None,
        ) or []
        return _json({
            "graph_id": gid,
            "center": rcenter,
            "causal_only": bool(causal_only),
            "edge_count": len(edges),
            "edges": edges,
        })
    except Exception as exc:  # noqa: BLE001
        return _err("kg_n_hop_subgraph", exc)


def kg_trace_cascade(
    graph_id: str = "",
    source: str = "",
    target: str = "",
    center: str = "",
    causal_only: bool = True,
) -> str:
    """沿图谱追踪传导/级联链条（source+target → 有向路径；center → 多跳因果邻域），可读文本。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    try:
        svc = _zep_tools()
        # legacy trace_cascade 自带名称解析、因果放宽与友好降级串，直接透传。
        return svc.trace_cascade(
            gid,
            source=(source or "").strip(),
            target=(target or "").strip(),
            center=(center or "").strip(),
            causal_only=bool(causal_only),
        )
    except Exception as exc:  # noqa: BLE001
        return _err("kg_trace_cascade", exc)


def kg_centrality_priors(graph_id: str = "") -> str:
    """全图结构先验：归一度中心度 + 介数/咽喉节点 + 连通分量与枢纽统计，JSON 输出。"""
    gid = _resolve_graph_id(graph_id)
    if not gid:
        return _MISSING_GRAPH_MSG
    try:
        gb = _graph_builder()
        # _get_graph_info 是 legacy 中「build 之外」唯一的中心度/介数先验读路径
        # （P3-7/R2-KG-5 的产物只在建图 worker 内消费，无公开读 API）。此处复用而非
        # 重实现 Brandes/并查集；cutover 时应在 legacy 提为公开方法。
        info = gb._get_graph_info(gid)  # noqa: SLF001
        return _json(info.to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err("kg_centrality_priors", exc)
