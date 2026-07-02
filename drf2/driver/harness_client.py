"""Clients for the two external executors the driver sequences.

1. deer-flow 2.0 harness — primary transport is the Runs API over HTTP against ONE
   persistent thread (POST /api/threads/{id}/runs background, then deterministic
   status polling of GET /api/threads/{id}/runs/{run_id}; contract verified against
   deer-flow-2.0.0/backend/app/gateway/routers/thread_runs.py). We poll the run
   resource instead of the SSE ``join`` stream on purpose: status polling is
   restart-tolerant (a store-only hydrated run still answers GET, while join 409s)
   and needs no SSE parsing in the deterministic driver.
   TODO-for-cutover: embedded ``deerflow.client.DeerFlowClient`` transport as an
   in-process alternative for single-box deployments.

2. Simulation engine (drf2/engines/simulation job service) — the RUN stage is NOT a
   harness run (REDESIGN: the 60–120-min OASIS sim cannot live in a sub-agent turn);
   the driver calls sim_start and polls driver-side with a stall watchdog (ported
   from pipeline_orchestrator._spawn_run_stall_watchdog: no round advance within
   stall_s → force stop and fail honestly instead of hanging forever).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}  # runtime/runs/schemas.py


class HarnessError(RuntimeError):
    pass


@dataclass
class RunResult:
    run_id: str
    status: str
    error: Optional[str] = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "success"


class Harness(Protocol):
    def ensure_thread(self) -> None: ...
    def run_stage(self, stage: str, prompt: str, *, timeout_s: float,
                  context: Optional[dict[str, Any]] = None) -> RunResult: ...
    def artifact_path(self, relpath: str) -> Path: ...


class RunsApiHarness:
    """Drive one persistent gateway thread via the LangGraph-compatible Runs API."""

    def __init__(self, base_url: str, thread_id: str, *, user_id: str = "default",
                 workspace_root: Optional[str] = None, api_key: Optional[str] = None,
                 poll_interval_s: float = 5.0, http_timeout_s: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.thread_id = thread_id
        self.user_id = user_id
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self.api_key = api_key
        self.poll_interval_s = poll_interval_s
        self.http_timeout_s = http_timeout_s
        self._sleep = sleep

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.http_timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            raise HarnessError(f"{method} {path} → HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise HarnessError(f"{method} {path} failed: {e}") from e
        try:
            return json.loads(raw) if raw else {}
        except ValueError as e:
            raise HarnessError(f"{method} {path}: non-JSON response ({raw[:200]!r})") from e

    # ------------------------------------------------------------------ API
    def ensure_thread(self) -> None:
        # POST /api/threads 幂等：thread 已存在时返回既有记录（threads.py:create_thread）。
        self._request("POST", "/api/threads", {"thread_id": self.thread_id})

    def start_run(self, prompt: str, *, context: Optional[dict[str, Any]] = None) -> str:
        body: dict[str, Any] = {
            "input": {"messages": [{"role": "user", "content": prompt}]},
            # 后台运行：无 SSE 消费者，防御性声明 continue；单写者线程 → reject 并发即可。
            "on_disconnect": "continue",
            "multitask_strategy": "reject",
        }
        if context:
            body["context"] = context
        rec = self._request("POST", f"/api/threads/{self.thread_id}/runs", body)
        run_id = rec.get("run_id")
        if not run_id:
            raise HarnessError(f"run create returned no run_id: {rec}")
        return str(run_id)

    def poll_run(self, run_id: str, *, timeout_s: float) -> RunResult:
        start = time.monotonic()
        while True:
            rec = self._request("GET", f"/api/threads/{self.thread_id}/runs/{run_id}")
            status = str(rec.get("status") or "")
            elapsed = time.monotonic() - start
            if status in TERMINAL_RUN_STATUSES:
                err = None if status == "success" else f"run ended with status={status}"
                return RunResult(run_id=run_id, status=status, error=err, elapsed_s=elapsed)
            if elapsed > timeout_s:
                try:  # 超时先尽力取消，别把僵尸 run 留在网关里
                    self._request("POST", f"/api/threads/{self.thread_id}/runs/{run_id}/cancel")
                except HarnessError:
                    pass
                return RunResult(run_id=run_id, status="timeout",
                                 error=f"driver-side timeout after {int(elapsed)}s", elapsed_s=elapsed)
            self._sleep(self.poll_interval_s)

    def run_stage(self, stage: str, prompt: str, *, timeout_s: float,
                  context: Optional[dict[str, Any]] = None) -> RunResult:
        run_id = self.start_run(prompt, context=context)
        return self.poll_run(run_id, timeout_s=timeout_s)

    def artifact_path(self, relpath: str) -> Path:
        """Host-side path of a thread-workspace artifact (outputs contract:
        /mnt/user-data/outputs → users/{uid}/threads/{tid}/user-data/outputs)."""
        if self.workspace_root is None:
            raise HarnessError("workspace_root not configured — cannot resolve artifacts")
        return (self.workspace_root / "users" / self.user_id / "threads"
                / self.thread_id / "user-data" / "outputs" / relpath)


@dataclass
class DryRunHarness:
    """Offline fake for tests / CLI --dry-run: same surface as RunsApiHarness.

    ``stage_effects[stage]`` is called as ``effect(workspace, prompt)`` before
    returning, so fixtures can materialize exactly the artifacts a real skill run
    would write (the prompt carries the target subdir for ensemble seeds).
    ``stage_statuses[stage]`` overrides the returned status (default "success").
    """

    workspace: Path
    stage_effects: dict[str, Callable[[Path, str], None]] = field(default_factory=dict)
    stage_statuses: dict[str, str] = field(default_factory=dict)
    prompts: list[tuple[str, str]] = field(default_factory=list)
    thread_id: str = "dry-run-thread"

    def ensure_thread(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def run_stage(self, stage: str, prompt: str, *, timeout_s: float,
                  context: Optional[dict[str, Any]] = None) -> RunResult:
        self.prompts.append((stage, prompt))
        effect = self.stage_effects.get(stage)
        if effect is not None:
            effect(self.workspace, prompt)
        status = self.stage_statuses.get(stage, "success")
        err = None if status == "success" else f"run ended with status={status}"
        return RunResult(run_id=f"dry-{stage}-{len(self.prompts)}", status=status, error=err)

    def artifact_path(self, relpath: str) -> Path:
        return self.workspace / relpath


# ---------------------------------------------------------------------------
# Simulation engine client (RUN stage — not a harness run)
# ---------------------------------------------------------------------------


class SimEngineError(RuntimeError):
    pass


class SimEngine(Protocol):
    def sim_start(self, config: dict[str, Any], *, seed: Optional[int] = None) -> str: ...
    def sim_status(self, sim_id: str) -> dict[str, Any]: ...
    def sim_stop(self, sim_id: str) -> None: ...


class HttpSimEngine:
    """HTTP client for the drf2 simulation job service (engines/simulation).

    Expected contract: POST {base}/simulations → {"sim_id"}, GET
    {base}/simulations/{id} → {"completed", "error", "current_round", ...},
    POST {base}/simulations/{id}/stop.
    TODO-for-cutover: pin these paths once drf2/engines/simulation lands its API.
    """

    def __init__(self, base_url: str, *, http_timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.http_timeout_s = http_timeout_s

    def _request(self, method: str, path: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.http_timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise SimEngineError(f"{method} {path} → HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise SimEngineError(f"{method} {path} failed: {e}") from e

    def sim_start(self, config: dict[str, Any], *, seed: Optional[int] = None) -> str:
        body: dict[str, Any] = {"config": config}
        if seed is not None:
            body["seed"] = seed
        rec = self._request("POST", "/simulations", body)
        sim_id = rec.get("sim_id") or rec.get("simulation_id")
        if not sim_id:
            raise SimEngineError(f"sim_start returned no sim_id: {rec}")
        return str(sim_id)

    def sim_status(self, sim_id: str) -> dict[str, Any]:
        return self._request("GET", f"/simulations/{sim_id}")

    def sim_stop(self, sim_id: str) -> None:
        try:
            self._request("POST", f"/simulations/{sim_id}/stop")
        except SimEngineError:
            pass  # 停止是尽力而为——看门狗语义（编排器同款：停失败只告警）


@dataclass
class DrySimEngine:
    """Offline fake: replays a scripted sequence of status dicts per started sim."""

    statuses: list[dict[str, Any]] = field(default_factory=lambda: [{"completed": True}])
    on_start: Optional[Callable[[dict[str, Any], Optional[int]], None]] = None
    started: list[tuple[dict[str, Any], Optional[int]]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    _cursor: int = 0

    def sim_start(self, config: dict[str, Any], *, seed: Optional[int] = None) -> str:
        self.started.append((config, seed))
        self._cursor = 0
        if self.on_start is not None:
            self.on_start(config, seed)
        return f"dry-sim-{len(self.started)}"

    def sim_status(self, sim_id: str) -> dict[str, Any]:
        idx = min(self._cursor, len(self.statuses) - 1)
        self._cursor += 1
        return self.statuses[idx]

    def sim_stop(self, sim_id: str) -> None:
        self.stopped.append(sim_id)


def poll_simulation(engine: SimEngine, sim_id: str, *, timeout_s: float,
                    stall_s: float = 1800.0, interval_s: float = 30.0,
                    clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Driver-side poll loop with the ported stall watchdog (QUALITY-OPT C1/C2):
    if ``current_round`` does not advance within ``stall_s``, force-stop the sim and
    fail honestly (legacy incident: dual-provider exhaustion wedged a sim for 4.5h
    while the pipeline still showed 'running'). Returns the final status dict.
    """
    start = clock()
    last_round: Any = object()  # sentinel ≠ 任何真实轮次
    last_progress = start
    while True:
        status = engine.sim_status(sim_id)
        if status.get("completed") or status.get("error"):
            return status
        now = clock()
        cur = status.get("current_round")
        if cur != last_round:
            last_round = cur
            last_progress = now
        elif stall_s > 0 and (now - last_progress) > stall_s:
            engine.sim_stop(sim_id)
            return {"completed": False,
                    "error": f"stall watchdog: no round advance in {int(stall_s)}s "
                             f"(stuck at round {cur})", **{k: v for k, v in status.items() if k != "error"}}
        if (now - start) > timeout_s:
            engine.sim_stop(sim_id)
            return {"completed": False,
                    "error": f"driver-side timeout after {int(now - start)}s"}
        sleep(interval_s)
