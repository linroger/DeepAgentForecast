# DRF-2 KG Engine (MCP server)

deer-flow 2.0 harness 的知识图谱引擎：以官方 python `mcp` SDK（FastMCP, stdio）把
legacy Graphiti/FalkorDB 栈（`backend/app/services/zep_tools.py` /
`graphiti_client/runtime.py` / `graph_builder.py`）封装成 8 个 MCP 工具。只封装、
不重实现图逻辑；所有 legacy 依赖在首次 `tools/call` 时懒加载，启动与 `tools/list`
完全离线。

## 运行

```bash
# 从仓库根目录（namespace 包 drf2 依赖 sys.path 含 repo root）
backend/.venv/bin/pip install -r drf2/engines/kg/requirements.txt   # 一次性
backend/.venv/bin/python -m drf2.engines.kg.server --graph-id <id>
```

`--graph-id` 设定进程默认图谱；每个工具也接受逐调用 `graph_id`（显式传入优先）。

## deer-flow 2.0 注册（extensions_config.json）

```json
{
  "mcpServers": {
    "drf2-kg": {
      "enabled": true,
      "type": "stdio",
      "command": "/abs/path/to/backend/.venv/bin/python",
      "args": ["-m", "drf2.engines.kg.server", "--graph-id", "<id>"],
      "env": {"PYTHONPATH": "/abs/path/to/repo-root"}
    }
  }
}
```

harness 的 stdio MCP 会话会 pin 子进程 CWD 到线程工作区，故用 `env.PYTHONPATH`
指向仓库根，保证 `drf2` namespace 包可导入。

## 工具面

| 工具 | 封装的 legacy 能力 | 输出 |
|---|---|---|
| `kg_add_episode` | `GraphitiRuntime.add_episode`（自动实体/关系抽取） | uuid 文本 |
| `kg_search` | `ZepToolsService.as_of_search` → `search_graph`（混合检索 + cross-encoder 重排 + 本地降级） | facts 文本 |
| `kg_get_entities` | `get_entities_by_type` / `get_all_nodes` | 实体列表文本 |
| `kg_get_edges` | `get_node_edges` / `get_all_edges`（双时态） | 边列表文本 |
| `kg_causal_paths` | `GraphitiRuntime.causal_paths`（逐跳 sign/strength/lag、净极性；无纯因果路径时放宽重试） | JSON |
| `kg_n_hop_subgraph` | `GraphitiRuntime.n_hop_subgraph` | JSON |
| `kg_trace_cascade` | `ZepToolsService.trace_cascade`（名称解析 + 友好降级串） | 可读文本 |
| `kg_centrality_priors` | `GraphBuilderService._get_graph_info`（度中心度 + 介数/咽喉先验） | JSON |

错误契约：任何后端异常折叠为 `KG engine error [tool]: …` 文本返回，stdio 进程
永不因单次工具失败退出。

## 测试

```bash
cd backend && .venv/bin/python -m pytest tests/test_drf2_kg.py -v   # 全离线
```
