"""DRF-2 KG 引擎 MCP 服务器测试（离线：mock zep_tools/graphiti 层，不触真图谱/LLM/网络）。

覆盖面（对齐 REDESIGN.md「KG engine」交付契约）：
- 工具注册表：8 个 kg_* 工具、schema 形状（required/optional 参数）、描述非空；
- 参数校验：graph_id 缺失、必填参数为空 → 友好错误文本（不抛异常）；
- 默认 graph_id 注入（--graph-id → set_default_graph_id）与逐调用覆盖；
- 各工具对 legacy 服务层的透传语义（含 kg_causal_paths 的因果放宽重试）；
- 错误面：服务层抛异常 → 折叠为 "KG engine error [...]" 文本（degrade-safe）；
- stdio 子进程冒烟：真实启动 `python -m drf2.engines.kg.server`，走 JSON-RPC
  initialize → tools/list（懒加载保证启动全离线，不初始化 Graphiti）。
"""

import asyncio
import json
import os
import subprocess
import sys
import threading

import pytest

# conftest 已把 backend/ 放进 sys.path；drf2 是 repo-root 下的 namespace 包，需补 repo root。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

pytest.importorskip("mcp", reason="官方 mcp SDK 未安装（drf2/engines/kg/requirements.txt）")

from drf2.engines.kg import server as kg_server  # noqa: E402
from drf2.engines.kg import tools as kg_tools  # noqa: E402

EXPECTED_TOOLS = {
    "kg_add_episode",
    "kg_search",
    "kg_get_entities",
    "kg_get_edges",
    "kg_causal_paths",
    "kg_n_hop_subgraph",
    "kg_trace_cascade",
    "kg_centrality_priors",
}


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """隔离 tools 模块的进程级状态：默认 graph_id 清空、懒加载单例注册表换新 dict。"""
    monkeypatch.setattr(kg_tools, "_DEFAULT_GRAPH_ID", "")
    monkeypatch.setattr(kg_tools, "_services", {})


# ─────────────────────────── 测试替身（不触任何真实图谱后端） ───────────────────────────


class FakeTextResult:
    def __init__(self, text="FAKE_SEARCH_TEXT"):
        self._text = text

    def to_text(self):
        return self._text


class FakeNode:
    def __init__(self, name, labels=None, uuid="u-1"):
        self.name = name
        self.labels = labels or ["Entity"]
        self.uuid = uuid

    def to_text(self):
        return f"实体: {self.name}"


class FakeEdge:
    def __init__(self, uuid="e-1", fact="A→B"):
        self.uuid = uuid
        self._fact = fact

    def to_text(self, include_temporal=False):
        suffix = "(含时效)" if include_temporal else ""
        return f"关系: {self._fact}{suffix}"


class FakeZepService:
    """ZepToolsService 的最小替身：记录调用、按脚本返回。"""

    _CAUSAL_EDGE_NAMES = ["CAUSES", "ENABLES"]

    def __init__(self):
        self.calls = []
        self._cache_lock = threading.RLock()
        self._nodes_cache = {}
        self._edges_cache = {}
        self.invalidated = []

    def as_of_search(self, graph_id, query, as_of, limit=10, scope="edges"):
        self.calls.append(("as_of_search", graph_id, query, as_of, limit, scope))
        return FakeTextResult()

    def get_all_nodes(self, graph_id):
        self.calls.append(("get_all_nodes", graph_id))
        return [FakeNode("甲"), FakeNode("乙", labels=["Org"], uuid="u-2")]

    def get_entities_by_type(self, graph_id, entity_type):
        self.calls.append(("get_entities_by_type", graph_id, entity_type))
        return [FakeNode("乙", labels=[entity_type], uuid="u-2")]

    def get_all_edges(self, graph_id, include_temporal=True):
        self.calls.append(("get_all_edges", graph_id, include_temporal))
        return [FakeEdge(), FakeEdge(uuid="e-2", fact="B→C")]

    def get_node_edges(self, graph_id, node_uuid):
        self.calls.append(("get_node_edges", graph_id, node_uuid))
        return [FakeEdge(uuid="e-3", fact="X→Y")]

    def trace_cascade(self, graph_id, source="", target="", center="", causal_only=True):
        self.calls.append(("trace_cascade", graph_id, source, target, center, causal_only))
        return "【传导路径：A → B】"

    def _resolve_entity_name(self, graph_id, name):
        self.calls.append(("_resolve_entity_name", graph_id, name))
        return f"规范::{name}"

    def invalidate_search_cache(self, graph_id=None):
        self.invalidated.append(graph_id)


class FakeRuntime:
    def __init__(self, causal_script=None):
        self.calls = []
        # causal_script: 依次弹出的 causal_paths 返回值（测因果放宽重试）
        self._causal_script = list(causal_script or [])

    def add_episode(self, graph_id, *, name, body, source_type, source_description, reference_time):
        self.calls.append(("add_episode", graph_id, name, body, source_type,
                           source_description, reference_time))
        return "ep-uuid-123"

    def causal_paths(self, graph_id, source, target, edge_types=None,
                     max_hops=4, limit=20, as_of=None):
        self.calls.append(("causal_paths", graph_id, source, target,
                           tuple(edge_types) if edge_types else None, max_hops, limit, as_of))
        if self._causal_script:
            return self._causal_script.pop(0)
        return [{"nodes": [source, target], "edges": ["CAUSES"], "hops": 1,
                 "edge_details": [], "net_polarity": "+", "lag_total": None}]

    def n_hop_subgraph(self, graph_id, center, max_hops=2, edge_types=None,
                       limit=60, as_of=None):
        self.calls.append(("n_hop_subgraph", graph_id, center,
                           tuple(edge_types) if edge_types else None, max_hops, limit, as_of))
        return [{"source": center, "edge": "ENABLES", "target": "B",
                 "sign": None, "strength": None, "polarity": None, "lag": None, "fact": "f"}]


class FakeGraphInfo:
    def to_dict(self):
        return {"graph_id": "g", "node_count": 2, "edge_count": 1,
                "centrality": {"甲": 1.0}, "betweenness": {}, "chokepoints": []}


class FakeGraphBuilder:
    def __init__(self):
        self.calls = []

    def _get_graph_info(self, graph_id):
        self.calls.append(("_get_graph_info", graph_id))
        return FakeGraphInfo()


def _install(monkeypatch, zep=None, runtime=None, builder=None):
    """把替身塞进 tools 的懒加载注册表（懒加载 getter 命中即返回，绝不 import 真后端）。"""
    if zep is not None:
        kg_tools._services["zep_tools"] = zep
    if runtime is not None:
        kg_tools._services["runtime"] = runtime
    if builder is not None:
        kg_tools._services["graph_builder"] = builder


# ────────────────────────────── 工具注册表 / schema 形状 ──────────────────────────────


class TestToolRegistry:
    def test_all_eight_tools_registered(self):
        server = kg_server.build_server()
        tools = asyncio.run(server.list_tools())
        assert {t.name for t in tools} == EXPECTED_TOOLS

    def test_schema_shapes(self):
        server = kg_server.build_server()
        by_name = {t.name: t for t in asyncio.run(server.list_tools())}

        # 必填参数（graph_id 恒为可选：可由 --graph-id 进程默认兜底）
        assert set(by_name["kg_add_episode"].inputSchema.get("required") or []) == {"body"}
        assert set(by_name["kg_search"].inputSchema.get("required") or []) == {"query"}
        assert set(by_name["kg_causal_paths"].inputSchema.get("required") or []) == {"source", "target"}
        assert set(by_name["kg_n_hop_subgraph"].inputSchema.get("required") or []) == {"center"}
        for name in ("kg_get_entities", "kg_get_edges", "kg_trace_cascade", "kg_centrality_priors"):
            assert not (by_name[name].inputSchema.get("required") or []), name

        for tool in by_name.values():
            props = tool.inputSchema.get("properties") or {}
            assert "graph_id" in props, f"{tool.name} 应支持逐调用 graph_id"
            assert "graph_id" not in (tool.inputSchema.get("required") or [])
            # deferred tool_search 依赖描述做语义匹配：必须非空且足够长
            assert tool.description and len(tool.description) > 60, tool.name

    def test_default_graph_id_injected_by_build_server(self):
        kg_server.build_server(default_graph_id="g-default")
        assert kg_tools._DEFAULT_GRAPH_ID == "g-default"


# ────────────────────────────── 参数校验（无后端接触） ──────────────────────────────


class TestArgValidation:
    def test_missing_graph_id_everywhere(self):
        # 未设默认 graph_id 且未逐调用传入 → 每个工具都返回同一友好错误，且不 import 后端
        cases = [
            kg_tools.kg_add_episode(body="text"),
            kg_tools.kg_search(query="q"),
            kg_tools.kg_get_entities(),
            kg_tools.kg_get_edges(),
            kg_tools.kg_causal_paths(source="a", target="b"),
            kg_tools.kg_n_hop_subgraph(center="a"),
            kg_tools.kg_trace_cascade(source="a", target="b"),
            kg_tools.kg_centrality_priors(),
        ]
        for out in cases:
            assert "graph_id 缺失" in out
        assert kg_tools._services == {}  # 校验失败前绝不懒加载后端

    def test_empty_required_values(self):
        kg_tools.set_default_graph_id("g")
        assert "query 不能为空" in kg_tools.kg_search(query="   ")
        assert "body 不能为空" in kg_tools.kg_add_episode(body="")
        assert "source 与 target" in kg_tools.kg_causal_paths(source="", target="b")
        assert "center 不能为空" in kg_tools.kg_n_hop_subgraph(center=" ")
        assert kg_tools._services == {}


# ────────────────────────────── 透传语义（mock 服务层） ──────────────────────────────


class TestPassThrough:
    def test_kg_search_uses_default_graph_and_clamps(self, monkeypatch):
        zep = FakeZepService()
        _install(monkeypatch, zep=zep)
        kg_tools.set_default_graph_id("g-default")
        out = kg_tools.kg_search(query="谁影响谁", limit=9999, scope="NODES", as_of="2026-05")
        assert out == "FAKE_SEARCH_TEXT"
        kind, gid, query, as_of, limit, scope = zep.calls[0]
        assert (kind, gid, query) == ("as_of_search", "g-default", "谁影响谁")
        assert as_of == "2026-05"
        assert limit == 50          # clamp 到上限
        assert scope == "nodes"     # 大小写归一
        # 逐调用 graph_id 覆盖进程默认
        kg_tools.kg_search(query="q2", graph_id="g-override")
        assert zep.calls[1][1] == "g-override"

    def test_kg_add_episode_returns_uuid_and_invalidates_caches(self, monkeypatch):
        zep = FakeZepService()
        zep._nodes_cache["g1"] = ["stale"]
        zep._edges_cache[("g1", True)] = ["stale"]
        rt = FakeRuntime()
        _install(monkeypatch, zep=zep, runtime=rt)
        out = kg_tools.kg_add_episode(body="新事实文本", name="研究简报", graph_id="g1")
        assert "ep-uuid-123" in out and "g1" in out
        (_, gid, name, body, source_type, source_desc, ref_time) = rt.calls[0]
        assert (gid, name, body, source_type) == ("g1", "研究简报", "新事实文本", "text")
        assert source_desc == "drf2-kg-mcp"
        assert ref_time is None  # 未传 reference_time → 不做日期解析（不触 legacy 导入）
        # 写图后使已建单例的读缓存失效
        assert zep.invalidated == ["g1"]
        assert "g1" not in zep._nodes_cache
        assert ("g1", True) not in zep._edges_cache

    def test_kg_get_entities_all_and_by_type(self, monkeypatch):
        zep = FakeZepService()
        _install(monkeypatch, zep=zep)
        out_all = kg_tools.kg_get_entities(graph_id="g")
        assert "共 2 个实体" in out_all and "实体: 甲" in out_all and "uuid=u-1" in out_all
        out_typed = kg_tools.kg_get_entities(graph_id="g", entity_type="Org")
        assert "类型=Org" in out_typed and "实体: 乙" in out_typed
        assert zep.calls[1] == ("get_entities_by_type", "g", "Org")

    def test_kg_get_edges_all_and_by_node(self, monkeypatch):
        zep = FakeZepService()
        _install(monkeypatch, zep=zep)
        out_all = kg_tools.kg_get_edges(graph_id="g")
        assert "共 2 条边" in out_all and "(含时效)" in out_all
        out_node = kg_tools.kg_get_edges(graph_id="g", node_uuid="u-9", include_temporal=False)
        assert "节点 u-9" in out_node and "X→Y" in out_node and "(含时效)" not in out_node
        assert zep.calls[1] == ("get_node_edges", "g", "u-9")

    def test_kg_causal_paths_resolves_names_and_returns_json(self, monkeypatch):
        zep = FakeZepService()
        rt = FakeRuntime()
        _install(monkeypatch, zep=zep, runtime=rt)
        out = kg_tools.kg_causal_paths(source="a", target="b", graph_id="g", max_hops=99)
        payload = json.loads(out)
        assert payload["source"] == "规范::a" and payload["target"] == "规范::b"
        assert payload["path_count"] == 1 and payload["paths"][0]["net_polarity"] == "+"
        call = rt.calls[0]
        assert call[4] == ("CAUSES", "ENABLES")  # causal_only=True → 因果族边过滤
        assert call[5] == 6  # max_hops clamp 到 runtime 上限 6

    def test_kg_causal_paths_relaxes_when_no_causal_path(self, monkeypatch):
        zep = FakeZepService()
        # 第一次（因果族过滤）返回空 → 放宽为全部关系重试
        relaxed = [{"nodes": ["规范::a", "规范::b"], "edges": ["RELATES"], "hops": 1,
                    "edge_details": [], "net_polarity": None, "lag_total": None}]
        rt = FakeRuntime(causal_script=[[], relaxed])
        _install(monkeypatch, zep=zep, runtime=rt)
        payload = json.loads(kg_tools.kg_causal_paths(source="a", target="b", graph_id="g"))
        assert payload["path_count"] == 1
        assert "一般关系路径" in payload["note"]
        assert rt.calls[0][4] == ("CAUSES", "ENABLES") and rt.calls[1][4] is None

    def test_kg_n_hop_subgraph_json(self, monkeypatch):
        zep = FakeZepService()
        rt = FakeRuntime()
        _install(monkeypatch, zep=zep, runtime=rt)
        payload = json.loads(kg_tools.kg_n_hop_subgraph(center="a", graph_id="g", causal_only=True))
        assert payload["center"] == "规范::a" and payload["edge_count"] == 1
        assert rt.calls[0][3] == ("CAUSES", "ENABLES")

    def test_kg_trace_cascade_passthrough(self, monkeypatch):
        zep = FakeZepService()
        _install(monkeypatch, zep=zep)
        out = kg_tools.kg_trace_cascade(graph_id="g", source="a", target="b", causal_only=False)
        assert out == "【传导路径：A → B】"
        assert zep.calls[0] == ("trace_cascade", "g", "a", "b", "", False)

    def test_kg_centrality_priors_json(self, monkeypatch):
        gb = FakeGraphBuilder()
        _install(monkeypatch, builder=gb)
        payload = json.loads(kg_tools.kg_centrality_priors(graph_id="g"))
        assert payload["centrality"] == {"甲": 1.0} and payload["node_count"] == 2
        assert gb.calls == [("_get_graph_info", "g")]


# ────────────────────────────── 错误面（degrade-safe） ──────────────────────────────


class _Boom:
    """任何方法调用都抛异常的服务替身。"""

    _CAUSAL_EDGE_NAMES = ["CAUSES"]

    def __getattr__(self, item):
        def _raise(*args, **kwargs):
            raise RuntimeError("backend exploded")
        return _raise


class TestErrorSurfacing:
    @pytest.mark.parametrize("call,tool_name", [
        (lambda: kg_tools.kg_search(query="q", graph_id="g"), "kg_search"),
        (lambda: kg_tools.kg_get_entities(graph_id="g"), "kg_get_entities"),
        (lambda: kg_tools.kg_get_edges(graph_id="g"), "kg_get_edges"),
        (lambda: kg_tools.kg_trace_cascade(graph_id="g", center="a"), "kg_trace_cascade"),
    ])
    def test_zep_layer_error_becomes_text(self, monkeypatch, call, tool_name):
        _install(monkeypatch, zep=_Boom())
        out = call()
        assert out.startswith(f"KG engine error [{tool_name}]")
        assert "backend exploded" in out

    def test_runtime_layer_error_becomes_text(self, monkeypatch):
        _install(monkeypatch, zep=_Boom(), runtime=_Boom())
        assert kg_tools.kg_add_episode(body="t", graph_id="g").startswith(
            "KG engine error [kg_add_episode]")
        assert kg_tools.kg_causal_paths(source="a", target="b", graph_id="g").startswith(
            "KG engine error [kg_causal_paths]")
        assert kg_tools.kg_n_hop_subgraph(center="a", graph_id="g").startswith(
            "KG engine error [kg_n_hop_subgraph]")

    def test_builder_layer_error_becomes_text(self, monkeypatch):
        _install(monkeypatch, builder=_Boom())
        assert kg_tools.kg_centrality_priors(graph_id="g").startswith(
            "KG engine error [kg_centrality_priors]")

    def test_call_tool_via_server_never_raises(self, monkeypatch):
        _install(monkeypatch, zep=_Boom())
        server = kg_server.build_server()
        content, structured = asyncio.run(server.call_tool("kg_search", {"query": "q", "graph_id": "g"}))
        assert content and content[0].text.startswith("KG engine error [kg_search]")
        assert structured["result"].startswith("KG engine error")


# ────────────────────────────── MCP 层集成（进程内 + 子进程） ──────────────────────────────


class TestMcpIntegration:
    def test_call_tool_happy_path(self, monkeypatch):
        zep = FakeZepService()
        _install(monkeypatch, zep=zep)
        server = kg_server.build_server(default_graph_id="g-default")
        content, structured = asyncio.run(server.call_tool("kg_search", {"query": "问题"}))
        assert content[0].text == "FAKE_SEARCH_TEXT"
        assert structured == {"result": "FAKE_SEARCH_TEXT"}
        assert zep.calls[0][1] == "g-default"

    def test_stdio_server_boots_and_lists_tools(self):
        """子进程冒烟：真实 stdio 传输走 initialize → tools/list（全离线——
        工具层懒加载保证启动不触 Graphiti/FalkorDB/嵌入模型）。"""
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
            [sys.executable, "-m", "drf2.engines.kg.server", "--graph-id", "smoke-graph"],
            input=requests,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,   # namespace 包 drf2 依赖 repo root 在 sys.path（-m 会加 CWD）
            timeout=120,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
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
        init_result = responses[1]["result"]
        assert init_result["serverInfo"]["name"] == "drf2-kg"
        assert 2 in responses, f"未收到 tools/list 响应；stderr:\n{proc.stderr[-2000:]}"
        tool_names = {t["name"] for t in responses[2]["result"]["tools"]}
        assert tool_names == EXPECTED_TOOLS
