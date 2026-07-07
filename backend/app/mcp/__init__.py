"""app.mcp — DeerFlow harness 用的 MCP 服务器包（ITEM 4）。

把后端两大能力以官方 ``mcp`` Python SDK（FastMCP，stdio 传输）暴露给 deer-flow 2.0
harness，让 lead agent 能像调用普通工具一样调用知识图谱检索与模拟采访：

- ``app.mcp.kg_server``  —— 包裹 ``app.services.zep_tools.ZepToolsService`` 已「工具化」
  的图谱函数（search_graph / trace_cascade（因果路径 + n-hop 邻域）/ 度中心性先验 /
  实体查询 / 图谱统计）。
- ``app.mcp.sim_server`` —— 包裹 ``app.services.simulation_runner.SimulationRunner`` 的
  模拟 采访 / 状态 / 结果 API（sim_interview_agents / sim_status / sim_results）。

设计原则（两台服务器一致）：
1. 启动与 ``tools/list`` **绝不**触碰 FalkorDB / Graphiti / 模拟 IPC——所有重依赖都在
   首次 ``tools/call`` 时才懒加载。这样即便 bridge research 早于任何图谱/模拟存在就把
   服务器拉起（harness 常见时序），进程也能干净启动、正常列工具，不会拖垮研究流程。
2. 每个工具都把异常折叠成**结构化错误**返回（``{"ok": False, "error": ...}``），并用
   ``asyncio.wait_for`` 给底层同步调用套一个超时上限——既不把 ToolError 堆栈抛给协议层，
   也**绝不 hang**（图谱/存储缺失时快速返回可读原因）。

模块导入本身是轻量的（不触发 SDK / 后端服务构造），故 ``import app.mcp.kg_server`` 在缺少
``mcp`` SDK 的环境下也只在 ``build_server()`` / ``main()`` 阶段报出指向 requirements 的清晰错误。
"""

__all__ = ["kg_server", "sim_server"]
