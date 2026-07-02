"""DRF-2 KG 引擎：把既有 Graphiti/FalkorDB 图谱栈以 MCP 服务器形式暴露给 deer-flow 2.0 harness。

本包只做「封装 + 传输」两件事：
- ``tools``  —— 8 个 MCP 工具的实现（惰性 import legacy ``app.services.*``，不重实现图逻辑）
- ``server`` —— FastMCP stdio 服务器入口（``python -m drf2.engines.kg.server --graph-id <id>``）

注意：``drf2``/``drf2.engines`` 是 namespace package（无 __init__.py），避免与
simulation 引擎子树产生共享文件冲突。
"""
