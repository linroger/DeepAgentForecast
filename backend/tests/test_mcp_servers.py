"""app.mcp.{kg_server,sim_server} MCP 服务器测试（ITEM 4）。

全离线：stub 掉 ZepToolsService 与 SimulationRunner，不触真图谱/FalkorDB/模拟 IPC/LLM/网络。

覆盖面：
- 工具注册表：kg 6 工具 / sim 3 工具、schema 形状（真实入参暴露、graph_id/sim_id 可选、必填正确）；
- 可用性纪律：graph_id/sim_id 缺失 → 结构化错误 {ok:false}（不抛异常）；
- 默认 id 注入（--graph-id/--sim-id / 环境变量）与逐调用覆盖；
- 各工具对 legacy 服务层的透传语义（stub 校验参数与返回）；
- degrade-safe：服务构造失败 → 结构化错误；工具调用超时 → TimeoutError 结构化错误（绝不 hang）；
- 采访可用性：模拟环境不在线 → 立即结构化错误（不空等满 timeout）；
- MCP 握手：官方 SDK 内存客户端 initialize → tools/list → call_tool；
- stdio 子进程冒烟：真实启动 `python -m app.mcp.*`，走 JSON-RPC initialize → tools/list（懒加载保证启动全离线）。
"""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

# conftest 已把 backend/ 放进 sys.path；repo root 也补上（extensions env 里用 repo root/backend 双路径）。
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
for _p in (BACKEND_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("mcp", reason="官方 mcp SDK 未安装（backend/requirements.txt: mcp）")

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from app.mcp import kg_server  # noqa: E402
from app.mcp import sim_server  # noqa: E402

KG_TOOLS = {
    "kg_search", "kg_trace_cascade", "kg_entity_summary",
    "kg_get_entities", "kg_centrality_priors", "kg_graph_statistics",
}
SIM_TOOLS = {"sim_status", "sim_results", "sim_interview_agents"}


# ─────────────────────────── 测试替身（不触任何真实后端） ───────────────────────────


class _FakeText:
    def __init__(self, text):
        self._text = text

    def to_text(self):
        return self._text


class _FakeNode:
    def __init__(self, uuid, name, labels=None, summary=""):
        self.uuid = uuid
        self.name = name
        self.labels = labels or ["Entity"]
        self.summary = summary


class _FakeEdge:
    def __init__(self, s, t):
        self.source_node_uuid = s
        self.target_node_uuid = t


class FakeZep:
    """记录调用并返回确定性结果的 ZepToolsService 替身。"""

    def __init__(self):
        self.calls = []

    def search_graph(self, graph_id, query, limit, scope):
        self.calls.append(("search_graph", graph_id, query, limit, scope))
        return _FakeText(f"SEARCH::{query}")

    def as_of_search(self, graph_id, query, as_of, limit, scope):
        self.calls.append(("as_of_search", graph_id, query, as_of, limit, scope))
        return _FakeText(f"ASOF::{query}::{as_of}")

    def trace_cascade(self, graph_id, source, target, center, causal_only):
        self.calls.append(("trace_cascade", graph_id, source, target, center, causal_only))
        return f"CASCADE::{source}->{target}|{center}"

    def get_entity_summary(self, graph_id, entity_name):
        self.calls.append(("get_entity_summary", graph_id, entity_name))
        return {"entity_name": entity_name, "total_relations": 2}

    def get_entities_by_type(self, graph_id, entity_type):
        self.calls.append(("get_entities_by_type", graph_id, entity_type))
        return [_FakeNode("u2", "乙", labels=[entity_type], summary="s")]

    def get_all_nodes(self, graph_id):
        self.calls.append(("get_all_nodes", graph_id))
        return [_FakeNode("u1", "甲", labels=["Org"], summary="s1"),
                _FakeNode("u2", "乙", labels=["Entity"], summary="s2")]

    def get_all_edges(self, graph_id):
        self.calls.append(("get_all_edges", graph_id))
        return [_FakeEdge("u1", "u2"), _FakeEdge("u1", "u2")]

    def get_graph_statistics(self, graph_id):
        self.calls.append(("get_graph_statistics", graph_id))
        return {"total_nodes": 2, "total_edges": 2, "entity_types": {"Org": 1}, "relation_types": {}}


@pytest.fixture(autouse=True)
def _isolate_kg_state(monkeypatch):
    """隔离 kg_server 进程级状态：默认图谱清空、服务单例/错误缓存复位。"""
    monkeypatch.setattr(kg_server, "_DEFAULT_GRAPH_ID", "")
    monkeypatch.setattr(kg_server, "_SERVICE", None)
    monkeypatch.setattr(kg_server, "_SERVICE_ERROR", None)
    monkeypatch.delenv("DRF_MCP_KG_GRAPH_ID", raising=False)
    monkeypatch.delenv("DRF_MCP_KG_TIMEOUT", raising=False)


@pytest.fixture(autouse=True)
def _isolate_sim_state(monkeypatch):
    monkeypatch.setattr(sim_server, "_DEFAULT_SIM_ID", "")
    monkeypatch.delenv("DRF_MCP_SIM_ID", raising=False)
    monkeypatch.delenv("DRF_MCP_SIM_TIMEOUT", raising=False)


def _install_kg(monkeypatch, svc):
    """把 kg_server 的懒加载服务单例直接替换成 stub（跳过 ZepToolsService 构造）。"""
    monkeypatch.setattr(kg_server, "_SERVICE", svc)
    monkeypatch.setattr(kg_server, "_SERVICE_ERROR", None)


# ══════════════════════════════ KG 服务器 ══════════════════════════════


class TestKgRegistration:
    def test_all_six_tools_registered(self):
        server = kg_server.build_server()
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert names == KG_TOOLS

    def test_schema_shapes(self):
        server = kg_server.build_server()
        by_name = {t.name: t for t in asyncio.run(server.list_tools())}
        # kg_search 暴露真实入参、仅 query 必填、graph_id 可选（FastMCP 反射修复回归）。
        search = by_name["kg_search"].inputSchema
        assert set(search["properties"]) == {"query", "limit", "scope", "as_of", "graph_id"}
        assert search.get("required") == ["query"]
        # graph_id 恒为可选（回退进程默认）。
        assert "graph_id" not in by_name["kg_graph_statistics"].inputSchema.get("required", [])
        # 描述非空（供 deferred tool_search 语义匹配）。
        for t in asyncio.run(server.list_tools()):
            assert t.description and len(t.description) > 40


class TestKgGraphIdResolution:
    def test_missing_graph_id_returns_structured_error(self):
        # 无默认、无逐调用 → 结构化错误（不抛异常）。
        out = asyncio.run(kg_server.kg_graph_statistics())
        assert out["ok"] is False
        assert "graph_id" in out["error"]

    def test_default_graph_id_from_build_server(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        kg_server.build_server(default_graph_id="g-default")
        out = asyncio.run(kg_server.kg_graph_statistics())
        assert out["ok"] is True and out["graph_id"] == "g-default"
        assert svc.calls[-1] == ("get_graph_statistics", "g-default")

    def test_default_graph_id_from_env(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        monkeypatch.setenv("DRF_MCP_KG_GRAPH_ID", "g-env")
        out = asyncio.run(kg_server.kg_graph_statistics())
        assert out["ok"] is True and out["graph_id"] == "g-env"

    def test_per_call_overrides_default(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        kg_server.build_server(default_graph_id="g-default")
        out = asyncio.run(kg_server.kg_graph_statistics(graph_id="g-override"))
        assert out["graph_id"] == "g-override"


class TestKgToolPassthrough:
    def test_search_edges(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_search(query="谁影响谁", limit=7, scope="edges", graph_id="g1"))
        assert out == {"ok": True, "graph_id": "g1", "result": "SEARCH::谁影响谁"}
        assert svc.calls[0] == ("search_graph", "g1", "谁影响谁", 7, "edges")

    def test_search_as_of_routes_to_temporal(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_search(query="q", as_of="2026-05", graph_id="g1"))
        assert out["result"] == "ASOF::q::2026-05"
        assert svc.calls[0][0] == "as_of_search"

    def test_trace_cascade(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_trace_cascade(source="A", target="B", graph_id="g1"))
        assert out["result"] == "CASCADE::A->B|"
        assert svc.calls[0] == ("trace_cascade", "g1", "A", "B", "", True)

    def test_entity_summary(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_entity_summary(entity_name="甲", graph_id="g1"))
        assert out["result"]["entity_name"] == "甲"

    def test_get_entities_all_and_by_type(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        all_out = asyncio.run(kg_server.kg_get_entities(graph_id="g1"))
        assert {n["name"] for n in all_out["result"]} == {"甲", "乙"}
        typed = asyncio.run(kg_server.kg_get_entities(entity_type="Org", graph_id="g1"))
        assert typed["result"][0]["type"] == "Org"

    def test_centrality_degree_prior(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_centrality_priors(top_k=5, graph_id="g1"))
        top = out["result"]["top_by_degree_centrality"]
        # 两条边都在 u1<->u2 之间：两节点度均为 2，归一化中心度 = 1.0。
        by_uuid = {r["uuid"]: r for r in top}
        assert by_uuid["u1"]["degree"] == 2
        assert by_uuid["u1"]["degree_centrality"] == 1.0
        assert out["result"]["total_nodes"] == 2

    def test_statistics(self, monkeypatch):
        svc = FakeZep()
        _install_kg(monkeypatch, svc)
        out = asyncio.run(kg_server.kg_graph_statistics(graph_id="g1"))
        assert out["result"]["total_edges"] == 2


class TestKgDegradeSafety:
    def test_service_construction_failure_is_structured(self, monkeypatch):
        # 未 stub 服务 + 强制构造失败：应返回结构化错误而非抛出。
        def _boom():
            raise RuntimeError("FalkorDB down")
        monkeypatch.setattr(kg_server, "_get_service", _boom)
        out = asyncio.run(kg_server.kg_graph_statistics(graph_id="g1"))
        assert out["ok"] is False
        assert "FalkorDB down" in out["error"]

    def test_absent_backend_message_on_import(self, monkeypatch):
        # _get_service 首次失败缓存原因，二次调用不重试构造，仍是结构化错误。
        monkeypatch.setattr(kg_server, "_SERVICE", None)
        monkeypatch.setattr(kg_server, "_SERVICE_ERROR", "知识图谱后端不可用（stub）")
        out = asyncio.run(kg_server.kg_search(query="q", graph_id="g1"))
        assert out["ok"] is False and "不可用" in out["error"]

    def test_timeout_returns_structured_error_not_hang(self, monkeypatch):
        # 慢服务：把超时钳到极小值，验证工具到点放弃并返回 TimeoutError 结构化错误（不 hang）。
        class SlowZep:
            def get_graph_statistics(self, graph_id):
                time.sleep(5)
                return {}
        _install_kg(monkeypatch, SlowZep())
        monkeypatch.setenv("DRF_MCP_KG_TIMEOUT", "0.2")

        async def _timed():
            # 在同一事件循环内测量「工具返回」耗时；不含 asyncio.run 收尾 join 掉线程池里
            # 仍在 sleep(5) 的 to_thread（真实长驻服务器的事件循环不会每次调用都 join）。
            t0 = time.monotonic()
            out = await kg_server.kg_graph_statistics(graph_id="g1")
            return out, time.monotonic() - t0

        out, elapsed = asyncio.run(_timed())
        assert out["ok"] is False and "TimeoutError" in out["error"]
        assert elapsed < 3  # ~0.2s 即返回，远小于 SlowZep 的 5s，证明工具面不 hang


class TestKgHandshake:
    def test_in_memory_initialize_list_and_call(self, monkeypatch):
        svc = FakeZep()
        server = kg_server.build_server(default_graph_id="g-hand")
        _install_kg(monkeypatch, svc)

        async def _run():
            async with create_connected_server_and_client_session(server) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "drf-kg"
                names = {t.name for t in (await session.list_tools()).tools}
                assert names == KG_TOOLS
                res = await session.call_tool("kg_search", {"query": "hello"})
                assert res.isError is False
                # FastMCP 把 dict 返回包在 structuredContent['result'] 下。
                payload = res.structuredContent["result"]
                assert payload["ok"] is True and payload["result"] == "SEARCH::hello"

        asyncio.run(_run())


# ══════════════════════════════ SIM 服务器 ══════════════════════════════


class _FakeRunState:
    def __init__(self, status):
        self._status = status

    @property
    def runner_status(self):
        class _S:
            def __init__(s, v):
                s.value = v
        return _S(self._status)

    def to_dict(self):
        return {"simulation_id": "s1", "runner_status": self._status}


class TestSimRegistration:
    def test_three_tools_registered(self):
        server = sim_server.build_server()
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert names == SIM_TOOLS

    def test_sim_id_optional_in_schema(self):
        server = sim_server.build_server()
        by_name = {t.name: t for t in asyncio.run(server.list_tools())}
        for name in SIM_TOOLS:
            assert "sim_id" not in by_name[name].inputSchema.get("required", [])


class TestSimIdResolution:
    def test_missing_sim_id_raises(self):
        # 无默认、无逐调用 → ValueError（工具面会折成结构化错误，见握手/闭包路径）。
        with pytest.raises(ValueError):
            sim_server._resolve_sim_id(None)

    def test_default_from_build_server(self):
        sim_server.build_server(default_sim_id="s-default")
        assert sim_server._resolve_sim_id(None) == "s-default"

    def test_default_from_env(self, monkeypatch):
        monkeypatch.setenv("DRF_MCP_SIM_ID", "s-env")
        assert sim_server._resolve_sim_id(None) == "s-env"

    def test_per_call_overrides_default(self):
        sim_server.build_server(default_sim_id="s-default")
        assert sim_server._resolve_sim_id("s-override") == "s-override"


class TestSimTools:
    def test_status_reads_run_state(self, monkeypatch):
        from app.services import simulation_runner as sr
        monkeypatch.setattr(sr.SimulationRunner, "get_run_state",
                            classmethod(lambda cls, sid: _FakeRunState("running")))
        monkeypatch.setattr(sr.SimulationRunner, "get_env_status_detail",
                            classmethod(lambda cls, sid: {"status": "alive"}))
        monkeypatch.setattr(sim_server, "_env_alive", lambda sid: True)
        out = sim_server._impl_status("s1")
        assert out["run_state"]["runner_status"] == "running"
        assert out["interview_ready"] is True

    def test_results_partial_flag_and_topn(self, monkeypatch, tmp_path):
        from app.services import simulation_runner as sr
        monkeypatch.setattr(sr.SimulationRunner, "get_run_state",
                            classmethod(lambda cls, sid: _FakeRunState("running")))
        monkeypatch.setattr(sr.SimulationRunner, "get_timeline",
                            classmethod(lambda cls, sid: [{"round_num": 0, "total_actions": 3}]))
        monkeypatch.setattr(sr.SimulationRunner, "get_agent_stats",
                            classmethod(lambda cls, sid: [
                                {"agent_name": "a", "total_actions": 1},
                                {"agent_name": "b", "total_actions": 9},
                            ]))
        monkeypatch.setattr(sim_server, "_sim_dir", lambda sid: str(tmp_path))
        out = sim_server._impl_results("s1", top_agents=1)
        assert out["partial"] is True  # running 非终态
        assert out["agent_stats"][0]["agent_name"] == "b"  # 按动作量排序取 Top-1

    def test_interview_rejects_dead_env_immediately(self, monkeypatch, tmp_path):
        # 环境不在线：不进入 SimulationRunner.interview_*，立即结构化错误（不空等 timeout）。
        monkeypatch.setattr(sim_server, "_sim_dir", lambda sid: str(tmp_path))
        monkeypatch.setattr(sim_server, "_env_alive", lambda sid: False)
        started = time.monotonic()
        out = sim_server._impl_interview("s1", prompt="hi", agent_id=None,
                                         interviews=None, platform=None, timeout=99)
        assert out["ok"] is False and "EnvironmentNotAlive" in out["error"]
        assert time.monotonic() - started < 2  # 未空等 99s

    def test_interview_missing_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sim_server, "_sim_dir", lambda sid: str(tmp_path / "nope"))
        out = sim_server._impl_interview("s1", prompt="hi", agent_id=None,
                                         interviews=None, platform=None, timeout=5)
        assert out["ok"] is False and "模拟不存在" in out["error"]

    def test_interview_batch_passthrough(self, monkeypatch, tmp_path):
        from app.services import simulation_runner as sr
        monkeypatch.setattr(sim_server, "_sim_dir", lambda sid: str(tmp_path))
        monkeypatch.setattr(sim_server, "_env_alive", lambda sid: True)
        seen = {}

        def _batch(cls, simulation_id, interviews, platform, timeout):
            seen.update(sid=simulation_id, n=len(interviews))
            return {"answers": len(interviews)}

        monkeypatch.setattr(sr.SimulationRunner, "interview_agents_batch", classmethod(_batch))
        out = sim_server._impl_interview("s1", prompt=None, agent_id=None,
                                         interviews=[{"agent_id": 1, "prompt": "q"}],
                                         platform=None, timeout=5)
        assert out["ok"] is True and out["result"]["answers"] == 1
        assert seen == {"sid": "s1", "n": 1}

    def test_interview_needs_some_mode(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sim_server, "_sim_dir", lambda sid: str(tmp_path))
        monkeypatch.setattr(sim_server, "_env_alive", lambda sid: True)
        out = sim_server._impl_interview("s1", prompt=None, agent_id=None,
                                         interviews=None, platform=None, timeout=5)
        assert out["ok"] is False and "采访参数不足" in out["error"]


class TestSimHandshake:
    def test_in_memory_initialize_and_status(self, monkeypatch):
        from app.services import simulation_runner as sr
        monkeypatch.setattr(sr.SimulationRunner, "get_run_state",
                            classmethod(lambda cls, sid: _FakeRunState("completed")))
        monkeypatch.setattr(sr.SimulationRunner, "get_env_status_detail",
                            classmethod(lambda cls, sid: {"status": "stopped"}))
        monkeypatch.setattr(sim_server, "_env_alive", lambda sid: False)
        server = sim_server.build_server(default_sim_id="s-hand")

        async def _run():
            async with create_connected_server_and_client_session(server) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "drf-simulation"
                names = {t.name for t in (await session.list_tools()).tools}
                assert names == SIM_TOOLS
                res = await session.call_tool("sim_status", {})
                assert res.isError is False
                payload = res.structuredContent["result"]
                assert payload["ok"] is True
                assert payload["result"]["run_state"]["runner_status"] == "completed"

        asyncio.run(_run())


# ══════════════════════════════ stdio 子进程冒烟 ══════════════════════════════


def _stdio_smoke(module: str, extra_args, expected_server_name, expected_tools):
    """真实 stdio 传输走 initialize → tools/list；懒加载保证启动全离线（不触后端）。"""
    requests = "\n".join([
        json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest-smoke", "version": "0"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ]) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", module, *extra_args],
        input=requests,
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,  # `python -m app.mcp.*` 需 backend 在 sys.path（-m 加 CWD）
        timeout=120,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": BACKEND_DIR},
    )
    responses = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg:
            responses[msg["id"]] = msg
    assert 1 in responses, f"未收到 initialize 响应；stderr:\n{proc.stderr[-2000:]}"
    assert responses[1]["result"]["serverInfo"]["name"] == expected_server_name
    assert 2 in responses, f"未收到 tools/list 响应；stderr:\n{proc.stderr[-2000:]}"
    tool_names = {t["name"] for t in responses[2]["result"]["tools"]}
    assert tool_names == expected_tools


class TestStdioSmoke:
    def test_kg_server_boots_and_lists(self):
        _stdio_smoke("app.mcp.kg_server", ["--graph-id", "smoke"], "drf-kg", KG_TOOLS)

    def test_sim_server_boots_and_lists(self):
        _stdio_smoke("app.mcp.sim_server", ["--sim-id", "smoke"], "drf-simulation", SIM_TOOLS)
