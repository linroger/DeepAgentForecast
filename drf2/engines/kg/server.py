"""DRF-2 KG 引擎 MCP 服务器（stdio 传输，官方 python ``mcp`` SDK / FastMCP）。

运行方式（须从仓库根目录启动，namespace 包 ``drf2`` 依赖 sys.path 含 repo root；
``python -m`` 会自动把 CWD 加入 sys.path）：

    /path/to/backend/.venv/bin/python -m drf2.engines.kg.server --graph-id <id>

deer-flow 2.0 侧在 extensions_config.json 里注册为 stdio MCP server::

    "drf2-kg": {
      "enabled": true,
      "type": "stdio",
      "command": "<repo>/backend/.venv/bin/python",
      "args": ["-m", "drf2.engines.kg.server", "--graph-id", "<id>"]
    }

设计要点：
- 服务器启动 / tools/list 不触碰 Graphiti/FalkorDB/嵌入模型——所有 legacy 依赖在
  首次 tools/call 时才由 ``tools.py`` 懒加载（deferred tool_search 只需 schema）。
- 工具实现层把异常折叠成错误文本返回（degrade-safe），stdio 进程永不因单次
  工具失败而退出。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 直接 `python drf2/engines/kg/server.py` 运行时的兜底：把 repo root 放进 sys.path，
# 使 `import drf2.…` 与 `-m` 启动等价。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from drf2.engines.kg import tools as kg_tools  # noqa: E402

SERVER_NAME = "drf2-kg"

INSTRUCTIONS = (
    "DRF-2 knowledge-graph engine backed by a local Graphiti/FalkorDB temporal "
    "knowledge graph. Tools cover ingestion (kg_add_episode), hybrid semantic "
    "search (kg_search), entity/edge reads (kg_get_entities, kg_get_edges), "
    "multi-hop causal reasoning (kg_causal_paths, kg_n_hop_subgraph, "
    "kg_trace_cascade) and structural priors (kg_centrality_priors). Every tool "
    "accepts an optional graph_id; when omitted, the server-level default graph "
    "(--graph-id) is used."
)

# 工具名 → (实现函数, LLM 面向描述)。描述为英文长句并覆盖关键词，服务 deferred
# tool_search 的语义匹配；实现函数的参数签名即 JSON schema 来源（FastMCP 反射）。
_TOOL_SPECS = (
    (
        "kg_add_episode",
        kg_tools.kg_add_episode,
        "Ingest one text episode into the temporal knowledge graph. The graph "
        "engine automatically extracts entities and relationships from the text "
        "and merges them with existing nodes. Args: body (required, the text to "
        "ingest), name (episode label), graph_id (optional, defaults to the "
        "server's graph), source_description (provenance note), reference_time "
        "(the date the content is about; ISO or loose formats like '2026-05' "
        "accepted). Returns the created episode uuid as text.",
    ),
    (
        "kg_search",
        kg_tools.kg_search,
        "Hybrid semantic + BM25 search over the knowledge graph with "
        "cross-encoder reranking. Returns the most relevant facts (edge facts "
        "and node summaries) as plain text. scope='edges' searches relationship "
        "facts (default); scope='nodes' searches entity summaries. Optional "
        "as_of restricts results to facts valid at that point in time "
        "(point-in-time / temporal query). Use this as the default retrieval "
        "tool for questions about what the graph knows.",
    ),
    (
        "kg_get_entities",
        kg_tools.kg_get_entities,
        "List entities (nodes) in the knowledge graph with name, ontology type "
        "and summary. Optional entity_type filters by ontology label (e.g. "
        "'PublicFigure', 'Organization'). Returns up to `limit` entities with "
        "their uuids for follow-up edge lookups.",
    ),
    (
        "kg_get_edges",
        kg_tools.kg_get_edges,
        "List relationship edges of the knowledge graph, each with its fact "
        "sentence and bi-temporal validity (valid_at / invalid_at / expired_at). "
        "Pass node_uuid to restrict to one entity's edges (get an entity's uuid "
        "from kg_get_entities or kg_search). Set include_temporal=false to omit "
        "validity windows.",
    ),
    (
        "kg_causal_paths",
        kg_tools.kg_causal_paths,
        "Find directed multi-hop causal paths from a source entity to a target "
        "entity (up to max_hops, default 4). Returns JSON with the node chain, "
        "per-hop causal attributes (sign, strength, polarity, lag), the "
        "computed net polarity and cumulative lag of each path. causal_only=true "
        "(default) restricts hops to causal edge families (CAUSES/ENABLES/"
        "CONSTRAINS/TRIGGERS/ACCELERATES) and falls back to general relations "
        "when no pure-causal path exists. Optional as_of applies point-in-time "
        "filtering. Use for 'how does X propagate to Y' reasoning.",
    ),
    (
        "kg_n_hop_subgraph",
        kg_tools.kg_n_hop_subgraph,
        "Return the N-hop neighborhood subgraph around a center entity as a "
        "deduplicated JSON edge list (source, edge, target, plus per-edge causal "
        "attributes sign/strength/polarity/lag and the fact sentence). "
        "max_hops defaults to 2 (max 4). Set causal_only=true to keep only "
        "causal-family edges. Use to map an entity's sphere of influence.",
    ),
    (
        "kg_trace_cascade",
        kg_tools.kg_trace_cascade,
        "Trace transmission / cascade chains through the knowledge graph and "
        "return human-readable path text. Two modes: pass source AND target to "
        "list directed paths between them (preferring causal edges, annotated "
        "with per-hop attributes); or pass center alone to list its multi-hop "
        "causal neighborhood. Entity names are resolved to canonical graph "
        "nodes automatically. Use for 'which node flips the outcome' analysis.",
    ),
    (
        "kg_centrality_priors",
        kg_tools.kg_centrality_priors,
        "Compute structural priors over the whole graph and return JSON: "
        "normalized degree centrality per node, betweenness centrality and "
        "chokepoint/articulation nodes (when enabled in config), plus node/edge "
        "counts, connected components, entity types and top hub nodes. Use to "
        "weight actors by structural influence before simulation or forecasting.",
    ),
)


def build_server(default_graph_id: str = ""):
    """构建并返回已注册全部 8 个 KG 工具的 FastMCP 实例（不启动传输）。"""
    # mcp SDK 在函数内导入：让 `import drf2.engines.kg.server` 在未装 SDK 的环境里
    # 也能失败得干净（错误信息指向 requirements.txt），而非模块级 ImportError 连坐。
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的可读报错
        raise ImportError(
            "官方 mcp SDK 未安装。请执行 "
            "`backend/.venv/bin/pip install -r drf2/engines/kg/requirements.txt`"
        ) from exc

    if default_graph_id:
        kg_tools.set_default_graph_id(default_graph_id)

    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    for name, fn, description in _TOOL_SPECS:
        server.tool(name=name, description=description)(fn)
    return server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="drf2-kg-server",
        description="DRF-2 KG engine MCP server (stdio) wrapping the local Graphiti/FalkorDB stack.",
    )
    parser.add_argument(
        "--graph-id",
        default="",
        help="进程默认图谱 ID（工具调用未显式传 graph_id 时使用；也可逐调用传入）。",
    )
    args = parser.parse_args(argv)
    server = build_server(default_graph_id=args.graph_id)
    # stdio 传输：stdout 只走 JSON-RPC，日志走 stderr（python logging 默认行为）。
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
