"""DRF-2 模拟引擎（OASIS job service + MCP tools）。

按 REDESIGN.md 第 2 节：多小时、有状态的 OASIS 模拟不能作为 harness 的
sub-agent / skill 脚本运行——它以独立进程跑 backend/scripts/run_parallel_simulation.py，
本包把「启动/进度/结果/停止/采访」以 MCP 工具暴露给 deer-flow 2.0 harness。

复用而非重写：进程管理、检查点/续跑、诚实的有机/种子记账、interview IPC
全部通过 import 复用 backend/app/services/simulation_runner.SimulationRunner。
文件契约不变：simulation_config.json → actions.jsonl / run_state.json /
run_summary.json / env_status.json。
"""

from .jobs import SimulationJobService

__all__ = ["SimulationJobService"]
