"""DRF-2 模拟引擎 MCP server（stdio）。

deer-flow 2.0 harness 通过 extensions_config.json 以 stdio 方式拉起本进程
（transport 契约见 deer-flow-2.0.0/backend/packages/harness/deerflow/mcp/client.py：
type=stdio 时必须提供 command）。启动方式：

    backend/.venv/bin/python drf2/engines/simulation/server.py

依赖：`mcp`（官方 Python SDK，见同目录 requirements.txt；backend/.venv 已装）。

设计要点：
- 长任务模型：sim_start 只负责拉起独立进程并立即返回 sim_id；
  进度一律走 sim_status 轮询（工具描述里写明，供 harness 的 deferred
  tool_search 正确选择）。
- 所有阻塞工作（子进程管理、文件 IO、IPC 等待）都丢进线程池
  （asyncio.to_thread），不阻塞 MCP 协议循环的 ping/心跳。
- 工具永不抛异常给协议层：失败统一返回 {"success": False, "error": ...}，
  让 lead agent 拿到可读的失败原因而非 ToolError 堆栈。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from drf2.engines.simulation.jobs import SimulationJobService  # noqa: E402

# stdio transport 下 stdout 是协议信道——日志必须只走 stderr。
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [drf2-simulation] %(levelname)s %(message)s")
logger = logging.getLogger("drf2.engines.simulation")

mcp = FastMCP(
    "drf2-simulation",
    instructions=(
        "OASIS multi-agent social simulation job service. Simulations are LONG jobs "
        "(60-120 min, 80 agents): sim_start returns immediately with a sim_id; poll "
        "sim_status for progress/health; fetch sim_results only after completion; "
        "sim_interview_agents works while the simulation process is alive (including "
        "its post-run wait-for-commands window)."
    ),
)


def _error_payload(exc: BaseException) -> Dict[str, Any]:
    """把异常压成 LLM 可消费的失败载荷（类型 + 消息，不带堆栈噪音）。"""
    return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
async def sim_start(
    config_path: Optional[str] = None,
    sim_id: Optional[str] = None,
    platform: str = "parallel",
    max_rounds: Optional[int] = None,
    sim_seed: Optional[int] = None,
    resume: Optional[bool] = None,
) -> Dict[str, Any]:
    """Start an OASIS simulation as a detached background job and RETURN IMMEDIATELY.

    This does NOT wait for the simulation (which runs 60-120 minutes in its own
    process): it spawns backend/scripts/run_parallel_simulation.py and returns a
    sim_id right away. Poll `sim_status` with that sim_id for progress; fetch
    `sim_results` after completion.

    Args:
        config_path: Path to a simulation_config.json (anywhere on disk; it is
            copied into a fresh simulation directory if needed). Mutually
            exclusive with sim_id.
        sim_id: ID of an already-prepared simulation directory (must contain
            simulation_config.json). Mutually exclusive with config_path.
        platform: "parallel" (default, Twitter+Reddit), "twitter" or "reddit".
        max_rounds: Optional cap on simulation rounds (truncation is recorded
            honestly in run_state).
        sim_seed: Deterministic sampling seed for this run (multi-seed ensemble
            support; injected only into the child process environment).
        resume: True = resume from the last per-round checkpoint (crash
            recovery); False = force a fresh run; None = auto-decide.

    Returns: {success, sim_id, sim_dir, run_state} — run_state.runner_status
    should be "running" on success.
    """
    try:
        return await asyncio.to_thread(
            SimulationJobService.start,
            sim_id=sim_id, config_path=config_path, platform=platform,
            max_rounds=max_rounds, sim_seed=sim_seed, resume=resume,
        )
    except Exception as exc:  # noqa: BLE001 — 工具面不向协议层抛异常
        logger.exception("sim_start failed")
        return _error_payload(exc)


@mcp.tool()
async def sim_status(sim_id: str) -> Dict[str, Any]:
    """Poll a running/finished OASIS simulation: round progress, action counts, health.

    This is the polling surface for the long-running job started by `sim_start`.
    Call it periodically (e.g. every 1-5 minutes) until progress.runner_status is
    a terminal state ("completed" / "failed" / "stopped").

    The `health` block detects stale/crashed jobs honestly:
    - "crashed": run_state still says running but the process is gone (host
      restart / SIGKILL) — restart with sim_start(resume=True) or archive with sim_stop.
    - "stale_heartbeat": process alive but run_state.json has not been refreshed
      within the threshold (monitor wedged).
    - "completed_with_errors": finished but some platform never emitted
      simulation_end.
    `health.interview_ready` tells whether sim_interview_agents can succeed now.

    Returns: {success, sim_id, found, health, env_status, progress, action_counts, run_state}.
    """
    try:
        return await asyncio.to_thread(SimulationJobService.status, sim_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sim_status failed")
        return _error_payload(exc)


@mcp.tool()
async def sim_results(sim_id: str) -> Dict[str, Any]:
    """Fetch final results of an OASIS simulation: run_summary + agent-dynamics summary.

    Use only after `sim_status` reports a terminal state (partial=true otherwise).
    run_summary carries HONEST accounting that downstream forecasting must respect:
    - organic_action_count vs seed_action_count (seed sign-ups/follows are NOT engagement),
    - simulation_health: ok | hollow | truncated | errored | llm_degraded
      ("hollow" = agents produced zero organic content — do not narrativize),
    - rounds_executed, top_agents, action_volume_by_round, top_posts.
    dynamics_summary (per platform) reports whether emotion/stance dynamics were
    actually active during the run.

    Returns: {success, sim_id, partial, run_summary, dynamics_summary, runner_status}.
    """
    try:
        return await asyncio.to_thread(SimulationJobService.results, sim_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sim_results failed")
        return _error_payload(exc)


@mcp.tool()
async def sim_stop(sim_id: str) -> Dict[str, Any]:
    """Stop a running OASIS simulation (terminates the whole process group).

    Honest semantics: the returned runner_status is "stopped" only if the
    process was verifiably terminated; if termination could not be confirmed the
    job is marked "failed" with an explanatory error instead of lying. Also
    handles orphans left by a previous host process (kill by persisted pid/pgid
    with cmdline identity check). Per-round checkpoints are preserved, so a
    stopped simulation can be resumed later via sim_start(resume=True).

    Returns: {success, sim_id, run_state}.
    """
    try:
        return await asyncio.to_thread(SimulationJobService.stop, sim_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sim_stop failed")
        return _error_payload(exc)


@mcp.tool()
async def sim_interview_agents(
    sim_id: str,
    prompt: Optional[str] = None,
    agent_id: Optional[int] = None,
    interviews: Optional[List[Dict[str, Any]]] = None,
    platform: Optional[str] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Interview simulated agents inside a LIVE OASIS simulation (blocking, up to `timeout`s).

    Bridges to the existing file-based interview IPC contract: questions are
    delivered into the running simulation process and answered in-character by
    the agents' LLMs. Requires the simulation process to be alive (check
    sim_status → health.interview_ready first); stale environments (process dead
    but env_status still "alive") are detected and rejected immediately instead
    of blocking the full timeout.

    Three modes:
    - interviews=[{"agent_id": int, "prompt": str, "platform"?: str}, ...]: batch;
    - agent_id + prompt: interview one agent;
    - prompt only: interview ALL agents with the same question (can take minutes;
      the effective timeout auto-scales with batch size).

    platform: "twitter" | "reddit" | None (None = both platforms in a parallel run).

    Returns the legacy interview payload plus sim_id and elapsed_seconds.
    """
    try:
        return await asyncio.to_thread(
            SimulationJobService.interview,
            sim_id=sim_id, prompt=prompt, agent_id=agent_id,
            interviews=interviews, platform=platform, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("sim_interview_agents failed")
        return _error_payload(exc)


if __name__ == "__main__":
    logger.info("drf2-simulation MCP server starting (stdio), run_root=%s",
                SimulationJobService.run_root())
    mcp.run(transport="stdio")
