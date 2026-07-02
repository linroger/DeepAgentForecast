# drf2/engines/simulation — OASIS 模拟引擎（MCP server）

REDESIGN.md「Simulation engine」框的实现：多小时、80-agent 的 OASIS 模拟以
**独立进程**运行（`backend/scripts/run_parallel_simulation.py`），本引擎把
job 生命周期以 MCP 工具暴露给 deer-flow 2.0 harness。进程管理/检查点续跑/
诚实记账/采访 IPC 全部通过 import 复用遗留 `SimulationRunner`——文件契约不变：

```
<run_root>/<sim_id>/
  simulation_config.json      # 输入（唯一必需）
  run_state.json              # 实时状态（监控线程 ~2s 心跳）
  twitter/actions.jsonl       # 动作日志（+ checkpoint.json 续跑检查点）
  reddit/actions.jsonl
  run_summary.json            # 终局摘要（organic/seed 记账 + simulation_health）
  env_status.json             # 采访窗口存活门（alive/stopped）
  ipc_commands/ ipc_responses/  # interview 文件 IPC
```

## 运行

```bash
backend/.venv/bin/python drf2/engines/simulation/server.py   # stdio MCP server
```

依赖：`mcp`（见 requirements.txt；backend/.venv 已含 1.24.0）。

## harness 注册（extensions_config.json）

```json
{
  "drf2-simulation": {
    "type": "stdio",
    "command": "/ABS/PATH/DeepResearchForecast/backend/.venv/bin/python",
    "args": ["/ABS/PATH/DeepResearchForecast/drf2/engines/simulation/server.py"]
  }
}
```

## 工具

| 工具 | 语义 |
|---|---|
| `sim_start` | 拉起独立模拟进程后**立即返回** sim_id（支持 config_path 或已备好的 sim_id；max_rounds / sim_seed / resume 透传） |
| `sim_status` | 轮询面：轮次进度、动作计数、健康度（crashed / stale_heartbeat / completed_with_errors）、采访可用性 |
| `sim_results` | run_summary（organic vs seed、simulation_health=hollow 等诚实信号）+ 平台 dynamics 摘要；终态缺摘要时现场聚合 |
| `sim_stop` | 终止整个进程组；STOPPED 只在确认杀掉后才报；检查点保留可续跑 |
| `sim_interview_agents` | 桥接既有 interview 文件 IPC；env 说 alive 但进程已死时立即拒绝并把 env_status 落 stopped |

## 测试

```bash
cd backend && ./.venv/bin/python -m pytest tests/test_drf2_simulation.py -q
```
