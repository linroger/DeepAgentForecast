"""Wave9-KG 单测：分页去重/终止、剪枝 keep-set 规划、GC 保留规划、UI 子图 top-K。

全部离线（offline-first）：fake client / 纯函数 / monkeypatch，绝不触真实图库或 LLM。
"""

import asyncio
import types

import pytest

from app.config import Config

# 先导入 services 包，规避既有的 zep_paging <-> graph_builder 循环导入顺序敏感性
# （app/services/__init__.py 会急切导入 graph_builder，而 graph_builder 导入 zep_paging；
# 若测试先导入 app.utils.zep_paging，其对 app.services.graphiti_client 的导入会触发
# services/__init__ 反向拉起尚未初始化完的 zep_paging）。
import app.services.graph_builder  # noqa: F401  (import-order guard)
from app.services.graphiti_client import runtime as _rt_mod


# ---------------------------------------------------------------------------
# 1) zep_paging：跨页 uuid 去重 + 「整页无新 uuid」终止护栏（falkordblite 游标 bug）
# ---------------------------------------------------------------------------
class _Item:
    def __init__(self, uuid):
        self.uuid_ = uuid
        self.uuid = uuid


class _FakePagingClient:
    """可编程的 Zep 假客户端：broken=True 时无视游标永远返回第一页（复现线上 bug）。"""

    def __init__(self, uuids, page_size=3, broken=False):
        self._uuids = sorted(uuids, reverse=True)  # DESC，与 graphiti 游标语义一致
        self._page_size = page_size
        self._broken = broken
        self.pages_served = 0
        ns_node = types.SimpleNamespace(get_by_graph_id=self._get_nodes)
        ns_edge = types.SimpleNamespace(get_by_graph_id=self._get_edges)
        self.graph = types.SimpleNamespace(node=ns_node, edge=ns_edge)

    def _page(self, limit, uuid_cursor):
        self.pages_served += 1
        if self._broken:
            # 复现 falkordblite bug：WHERE uuid < cursor 谓词被丢弃 → 永远第一页。
            return [_Item(u) for u in self._uuids[:limit]]
        if uuid_cursor is None:
            start = 0
        else:
            start = sum(1 for u in self._uuids if u > uuid_cursor)
            if uuid_cursor in self._uuids:
                start = self._uuids.index(uuid_cursor) + 1
        return [_Item(u) for u in self._uuids[start:start + limit]]

    def _get_nodes(self, graph_id, limit=100, uuid_cursor=None, **_):
        return self._page(limit, uuid_cursor)

    def _get_edges(self, graph_id, limit=100, uuid_cursor=None, **_):
        return self._page(limit, uuid_cursor)


def _uuids(n):
    return [f"uuid-{i:04d}" for i in range(n)]


class TestPagingDedupTermination:
    def test_healthy_pagination_returns_all_unique(self):
        from app.utils.zep_paging import fetch_all_nodes

        client = _FakePagingClient(_uuids(10), page_size=3)
        out = fetch_all_nodes(client, "g1", page_size=3)
        got = [x.uuid_ for x in out]
        assert sorted(got) == sorted(_uuids(10))
        assert len(got) == len(set(got))

    def test_broken_cursor_terminates_without_duplicates(self):
        """游标失效（每页相同）时：去重 + 立刻终止，绝不空转到 10,000 上限。"""
        from app.utils.zep_paging import fetch_all_edges

        client = _FakePagingClient(_uuids(50), page_size=5, broken=True)
        out = fetch_all_edges(client, "g1", page_size=5, max_items=10000)
        got = [x.uuid_ for x in out]
        assert len(got) == len(set(got)), "重复行绝不能被累积"
        assert len(got) == 5  # 只有第一页是可得的
        assert client.pages_served <= 3, "无新 uuid 的页必须立刻触发终止"

    def test_max_items_cap_still_enforced(self):
        from app.utils.zep_paging import fetch_all_nodes

        client = _FakePagingClient(_uuids(30), page_size=10)
        out = fetch_all_nodes(client, "g1", page_size=10, max_items=15)
        assert len(out) == 15


# ---------------------------------------------------------------------------
# 2) runtime._page_after_cursor：SKIP 等价游标定位（纯静态函数）
# ---------------------------------------------------------------------------
class TestPageAfterCursor:
    def test_cursor_semantics(self):
        from app.services.graphiti_client.runtime import GraphitiRuntime

        uuids = sorted(["a", "b", "c", "d", "e"], reverse=True)  # e,d,c,b,a
        page1 = GraphitiRuntime._page_after_cursor(uuids, None, 2)
        assert page1 == ["e", "d"]
        page2 = GraphitiRuntime._page_after_cursor(uuids, page1[-1], 2)
        assert page2 == ["c", "b"]
        page3 = GraphitiRuntime._page_after_cursor(uuids, page2[-1], 2)
        assert page3 == ["a"]
        assert GraphitiRuntime._page_after_cursor(uuids, "a", 2) == []

    def test_missing_cursor_falls_back_monotonically(self):
        from app.services.graphiti_client.runtime import GraphitiRuntime

        uuids = ["e", "d", "b", "a"]  # "c" 已被并发删除
        assert GraphitiRuntime._page_after_cursor(uuids, "c", 2) == ["b", "a"]


# ---------------------------------------------------------------------------
# 3) graph_pruner.plan_prune：keep-set / 删除决策（合成图，零副作用）
# ---------------------------------------------------------------------------
def _node(uuid, name, label="Organization"):
    return {"uuid": uuid, "name": name, "labels": ["Entity", label]}


def _edge(i, s, t):
    return {"uuid": f"e{i}", "name": "RELATES_TO",
            "source_node_uuid": s, "target_node_uuid": t}


@pytest.fixture
def synthetic_graph():
    """核心 A（别名 A-alias）；链 A-B-C-D；E 挂在 D 上（3 跳外、度 1）；
    孤立 X、Y；F-G 独立二元组（与核心无路径，各度 1）。"""
    nodes = [
        _node("nA", "Alpha Corp"),
        _node("nAalias", "ACME"),          # 核心别名 → 也算核心
        _node("nB", "Beta"),
        _node("nC", "Gamma"),
        _node("nD", "Delta"),
        _node("nE", "Epsilon"),
        _node("nX", "Orphan X"),
        _node("nY", "Orphan Y"),
        _node("nF", "Island F"),
        _node("nG", "Island G"),
    ]
    edges = [
        _edge(1, "nA", "nB"),
        _edge(2, "nB", "nC"),
        _edge(3, "nC", "nD"),
        _edge(4, "nD", "nE"),
        _edge(5, "nF", "nG"),
    ]
    actors = [{"name": "Alpha Corp", "type": "Organization", "aliases": ["ACME"]}]
    return nodes, edges, actors


class TestPlanPrune:
    def test_large_capacity_does_not_keep_unrelated_nodes(self, synthetic_graph):
        """容量是上限而不是填充目标；actor 半径外节点即使有空位也应删除。"""
        from app.services.graph_pruner import plan_prune

        nodes, edges, actors = synthetic_graph
        plan = plan_prune(nodes, edges, {}, actors,
                          max_entities=400, hops=2, min_degree=2, per_type_cap=150)
        assert plan["keep"] == {"nA", "nAalias", "nB", "nC"}
        assert set(plan["delete"]) == {"nD", "nE", "nX", "nY", "nF", "nG"}
        assert plan["stats"]["keep_count"] == 4

    def test_hard_cap_keeps_only_core_and_ranked_nhop_nodes(self, synthetic_graph):
        from app.services.graph_pruner import plan_prune

        nodes, edges, actors = synthetic_graph
        plan = plan_prune(nodes, edges, {}, actors,
                          max_entities=6, hops=2, min_degree=2, per_type_cap=150)
        keep, delete = plan["keep"], set(plan["delete"])
        # 核心（含别名节点）恒保留
        assert {"nA", "nAalias"} <= keep
        # 2-hop 邻域保留
        assert {"nB", "nC"} <= keep
        # 规划补集必须全部删除；任意长连通路径/较高度数不能绕过 actor-centered 半径。
        all_ids = {n["uuid"] for n in nodes}
        assert delete == all_ids - keep
        assert {"nD", "nE", "nX", "nY", "nF", "nG"} <= delete
        assert plan["stats"]["delete_by_reason"]["outside_core_hops"] == 6
        assert len(all_ids - delete) <= plan["stats"]["effective_cap"]

    def test_max_entities_cap_and_importance_order(self, synthetic_graph):
        from app.services.graph_pruner import plan_prune

        nodes, edges, actors = synthetic_graph
        mentions = {"nC": 10}  # nC 重要度最高（提及数）
        plan = plan_prune(nodes, edges, mentions, actors,
                          max_entities=3, hops=1, min_degree=2, per_type_cap=150)
        keep = plan["keep"]
        assert {"nA", "nAalias"} <= keep  # 核心不受容量约束
        # 容量 3 已被核心占 2，只剩 1 席：1-hop 内候选 nB 优先于 hop 外的 nC
        assert "nB" in keep
        assert plan["stats"]["keep_count"] == 3
        assert set(plan["delete"]) == {n["uuid"] for n in nodes} - keep

    def test_per_type_cap(self, synthetic_graph):
        from app.services.graph_pruner import plan_prune

        nodes, edges, actors = synthetic_graph
        plan = plan_prune(nodes, edges, {}, actors,
                          max_entities=400, hops=2, min_degree=2, per_type_cap=3)
        # Organization 类型上限 3（核心 2 个 + 1 席），其余同类型被 type_capped
        keep_types = sum(1 for u in plan["keep"])
        assert keep_types == 3
        assert plan["stats"]["type_capped"] > 0

    def test_large_connected_component_cannot_escape_hard_cap(self):
        from app.services.graph_pruner import plan_prune

        nodes = [_node(f"n{i}", "Core" if i == 0 else f"Node {i}") for i in range(1000)]
        edges = [_edge(i, f"n{i}", f"n{i + 1}") for i in range(999)]
        plan = plan_prune(
            nodes,
            edges,
            {},
            [{"name": "Core", "type": "Organization"}],
            max_entities=400,
            hops=2,
            min_degree=2,
            per_type_cap=1000,
        )
        all_ids = {n["uuid"] for n in nodes}
        survivors = all_ids - set(plan["delete"])

        assert survivors == plan["keep"]
        assert "n0" in survivors
        assert survivors == {"n0", "n1", "n2"}
        assert len(survivors) <= plan["stats"]["effective_cap"]
        assert plan["stats"]["max_hop_retained"] <= 2

    def test_core_actor_overflow_raises_effective_cap(self):
        from app.services.graph_pruner import plan_prune

        nodes = [_node(f"n{i}", f"Core {i}") for i in range(5)]
        actors = [{"name": f"Core {i}", "type": "Organization"} for i in range(5)]
        plan = plan_prune(
            nodes, [], {}, actors,
            max_entities=3, hops=1, min_degree=2, per_type_cap=1,
        )

        assert plan["keep"] == {f"n{i}" for i in range(5)}
        assert plan["delete"] == []
        assert plan["stats"]["effective_cap"] == 5
        assert plan["stats"]["cap_overridden_by_core"] is True

    def test_zero_hops_from_config_means_core_only(self, monkeypatch, synthetic_graph):
        from app.services.graph_pruner import plan_prune

        nodes, edges, actors = synthetic_graph
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES", 400, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CORE_ACTOR_HOPS", 0, raising=False)
        monkeypatch.setattr(Config, "GRAPH_PRUNE_MIN_DEGREE", 2, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES_PER_TYPE", 150, raising=False)

        plan = plan_prune(nodes, edges, {}, actors)

        assert plan["keep"] == {"nA", "nAalias"}
        assert plan["stats"]["params"]["hops"] == 0
        assert set(plan["delete"]) == {n["uuid"] for n in nodes} - plan["keep"]

    def test_no_core_matched_keeps_everything_deletable_empty(self, synthetic_graph):
        """prune_graph 层面在空核心集时整体跳过；plan 层面仍产出决策供检视。"""
        from app.services.graph_pruner import plan_prune

        nodes, edges, _ = synthetic_graph
        plan = plan_prune(nodes, edges, {}, [{"name": "Nonexistent"}],
                          max_entities=400, hops=2, min_degree=2, per_type_cap=150)
        assert plan["stats"]["core_count"] == 0


class TestPruneGraphGate:
    def test_disabled_knob_short_circuits(self, monkeypatch):
        from app.services import graph_pruner

        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", False, raising=False)
        audit = graph_pruner.prune_graph("g-any", [])
        assert audit["enabled"] is False
        assert audit["skipped_reason"] == "GRAPH_PRUNE_ENABLED=false"
        assert audit["deleted"] == 0

    def test_no_core_match_skips_deletion(self, monkeypatch, synthetic_graph):
        from app.services import graph_pruner

        nodes, edges, _ = synthetic_graph
        fake_rt = types.SimpleNamespace(
            all_entity_nodes=lambda gid: nodes,
            all_entity_edges=lambda gid: edges,
            mention_counts=lambda gid: {},
            delete_entity_nodes=lambda gid, uuids: pytest.fail("必须不触发删除"),
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)
        audit = graph_pruner.prune_graph("g-any", [{"name": "Nobody Here"}])
        assert audit["skipped_reason"] and "no core actors" in audit["skipped_reason"]
        assert audit["deleted"] == 0
        assert audit["delete_count"] == 0
        assert audit["kept"] == len(nodes)
        assert audit["keep_count"] == len(nodes)
        assert audit["actual_after"] == len(nodes)
        assert audit["degraded"] is True
        assert audit["delete_by_reason"] == {}
        assert audit["planned_delete_by_reason"]

    def test_low_core_actor_coverage_skips_destructive_prune(
        self, monkeypatch, synthetic_graph
    ):
        from app.services import graph_pruner

        nodes, edges, actors = synthetic_graph
        actors = actors + [{"name": "Missing Core Actor", "type": "Organization"}]
        fake_rt = types.SimpleNamespace(
            all_entity_nodes=lambda gid: nodes,
            all_entity_edges=lambda gid: edges,
            mention_counts=lambda gid: {},
            delete_entity_nodes=lambda gid, uuids: pytest.fail("低核心覆盖率不得触发删除"),
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_PRUNE_MIN_CORE_COVERAGE", 0.8, raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)

        audit = graph_pruner.prune_graph("g-low-coverage", actors)

        assert audit["core_actor_expected"] == 2
        assert audit["core_actor_matched"] == 1
        assert audit["core_actor_coverage"] == 0.5
        assert "coverage below" in audit["skipped_reason"]
        assert audit["delete_count"] == 0
        assert audit["actual_after"] == len(nodes)
        assert audit["degraded"] is True

    def test_prune_executes_deletion_via_runtime(self, monkeypatch, synthetic_graph, tmp_path):
        from app.services import graph_pruner

        nodes, edges, actors = synthetic_graph
        deleted_calls = []

        current_nodes = list(nodes)
        current_edges = list(edges)

        def _all_nodes(gid):
            return list(current_nodes)

        def _all_edges(gid):
            return list(current_edges)

        def _delete(gid, uuids):
            deleted_calls.append(list(uuids))
            doomed = set(uuids)
            current_nodes[:] = [n for n in current_nodes if n["uuid"] not in doomed]
            current_edges[:] = [
                e for e in current_edges
                if e["source_node_uuid"] not in doomed and e["target_node_uuid"] not in doomed
            ]
            return {"requested": len(uuids), "deleted": len(uuids), "failed": 0}

        fake_rt = types.SimpleNamespace(
            all_entity_nodes=_all_nodes,
            all_entity_edges=_all_edges,
            mention_counts=lambda gid: {},
            delete_entity_nodes=_delete,
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES", 6, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CORE_ACTOR_HOPS", 2, raising=False)
        monkeypatch.setattr(Config, "GRAPH_PRUNE_MIN_DEGREE", 2, raising=False)
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)
        audit = graph_pruner.prune_graph("g-prune", actors)
        assert audit["deleted"] == 6  # 仅核心 + 2-hop 的 nB/nC 保留
        assert deleted_calls and set(deleted_calls[0]) == {
            "nD", "nE", "nX", "nY", "nF", "nG"
        }
        assert audit["planned_keep"] == 4
        assert audit["actual_after"] == 4
        assert audit["survivor_set_matches_plan"] is True
        assert audit["core_survived"] is True
        assert audit["cap_satisfied"] is True
        assert audit["postcondition_ok"] is True
        assert audit.get("layout_recomputed") is True

    def test_partial_deletion_is_warning_and_failed_postcondition(
        self, monkeypatch, synthetic_graph, tmp_path
    ):
        from app.services import graph_pruner

        nodes, edges, actors = synthetic_graph
        warnings = []
        monkeypatch.setattr(
            graph_pruner.logger,
            "warning",
            lambda msg, *args: warnings.append(msg % args if args else str(msg)),
        )
        fake_rt = types.SimpleNamespace(
            all_entity_nodes=lambda gid: list(nodes),
            all_entity_edges=lambda gid: list(edges),
            mention_counts=lambda gid: {},
            delete_entity_nodes=lambda gid, uuids: {
                "requested": len(uuids), "deleted": 0, "failed": len(uuids)
            },
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES", 3, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CORE_ACTOR_HOPS", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES_PER_TYPE", 150, raising=False)
        monkeypatch.setattr(Config, "GRAPH_LAYOUT_PRECOMPUTE", False, raising=False)
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)

        audit = graph_pruner.prune_graph("g-partial", actors)

        assert audit["delete_retry_requested"] == audit["delete_count"]
        assert audit["delete_retry_failed"] == audit["delete_count"]
        assert audit["delete_failed"] == audit["delete_count"]
        assert audit["actual_after"] == len(nodes)
        assert audit["cap_satisfied"] is False
        assert audit["survivor_set_matches_plan"] is False
        assert audit["postcondition_ok"] is False
        assert audit["degraded"] is True
        assert "postcondition failed" in audit["error"]
        assert any("prune complete" in line for line in warnings)

    def test_post_delete_read_failure_is_explicit_verification_failure(
        self, monkeypatch, synthetic_graph, tmp_path
    ):
        from app.services import graph_pruner

        nodes, edges, actors = synthetic_graph
        node_reads = {"count": 0}
        warnings = []
        monkeypatch.setattr(
            graph_pruner.logger,
            "warning",
            lambda msg, *args: warnings.append(msg % args if args else str(msg)),
        )

        def _nodes(gid):
            node_reads["count"] += 1
            if node_reads["count"] > 1:
                raise RuntimeError("post-delete read unavailable")
            return list(nodes)

        fake_rt = types.SimpleNamespace(
            all_entity_nodes=_nodes,
            all_entity_edges=lambda gid: list(edges),
            mention_counts=lambda gid: {},
            delete_entity_nodes=lambda gid, uuids: {
                "requested": len(uuids), "deleted": len(uuids), "failed": 0
            },
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES", 3, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CORE_ACTOR_HOPS", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES_PER_TYPE", 150, raising=False)
        monkeypatch.setattr(Config, "GRAPH_LAYOUT_PRECOMPUTE", True, raising=False)
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)

        audit = graph_pruner.prune_graph("g-read-fail", actors)

        assert audit["actual_after"] is None
        assert audit["postcondition_ok"] is False
        assert audit["postcondition_issues"] == ["unable to read graph after deletion"]
        assert "post-delete read unavailable" in audit["verification_error"]
        assert audit["degraded"] is True
        assert audit.get("layout_recomputed") is not True
        assert any("post-prune verification failed" in line for line in warnings)

    def test_retry_never_expands_original_destructive_scope(self, monkeypatch, tmp_path):
        from app.services import graph_pruner

        current_nodes = [_node("core", "Core"), _node("old", "Old")]
        current_edges = []
        delete_calls = []

        def _delete(gid, uuids):
            delete_calls.append(list(uuids))
            doomed = set(uuids)
            current_nodes[:] = [n for n in current_nodes if n["uuid"] not in doomed]
            if len(delete_calls) == 1:
                current_nodes.append(_node("new", "Concurrent New"))
            return {"requested": len(uuids), "deleted": len(uuids), "failed": 0}

        fake_rt = types.SimpleNamespace(
            all_entity_nodes=lambda gid: list(current_nodes),
            all_entity_edges=lambda gid: list(current_edges),
            mention_counts=lambda gid: {},
            delete_entity_nodes=_delete,
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CORE_ACTOR_HOPS", 0, raising=False)
        monkeypatch.setattr(Config, "GRAPH_MAX_ENTITIES_PER_TYPE", 10, raising=False)
        monkeypatch.setattr(Config, "GRAPH_PRUNE_MIN_CORE_COVERAGE", 1.0, raising=False)
        monkeypatch.setattr(Config, "GRAPH_LAYOUT_PRECOMPUTE", False, raising=False)
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)

        audit = graph_pruner.prune_graph(
            "g-concurrent", [{"name": "Core", "type": "Organization"}]
        )

        assert delete_calls == [["old"]]
        assert {n["uuid"] for n in current_nodes} == {"core", "new"}
        assert audit["postcondition_ok"] is False
        assert audit["degraded"] is True
        assert audit["survivor_set_matches_plan"] is False

    def test_pre_mutation_failure_is_degraded_for_pipeline_health(
        self, monkeypatch, synthetic_graph
    ):
        from app.services import graph_pruner

        nodes, edges, actors = synthetic_graph
        fake_rt = types.SimpleNamespace(
            all_entity_nodes=lambda gid: nodes,
            all_entity_edges=lambda gid: edges,
            mention_counts=lambda gid: (_ for _ in ()).throw(RuntimeError("metrics down")),
        )
        monkeypatch.setattr(Config, "GRAPH_PRUNE_ENABLED", True, raising=False)
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)

        audit = graph_pruner.prune_graph("g-metrics-fail", actors)

        assert audit["degraded"] is True
        assert audit["postcondition_ok"] is False
        assert audit["mutation_state"] == "not_started"
        assert "metrics down" in audit["error"]


# ---------------------------------------------------------------------------
# 4) GC 保留规划（纯函数）
# ---------------------------------------------------------------------------
class TestGraphGcPlanning:
    def test_referenced_and_newest_retained(self):
        from app.services.graph_builder import plan_graph_gc

        all_ids = [f"g{i}" for i in range(8)]
        referenced = {"g0", "g1"}
        recency = {f"g{i}": f"2026-07-0{i}T00:00:00" for i in range(2, 8)}
        keep, victims = plan_graph_gc(all_ids, referenced, recency, retain=2)
        assert {"g0", "g1"} <= keep          # 被引用的恒保留
        assert {"g7", "g6"} <= keep          # 未引用中最新的 2 个保留
        assert set(victims) == {"g2", "g3", "g4", "g5"}

    def test_unknown_recency_treated_oldest(self):
        from app.services.graph_builder import plan_graph_gc

        keep, victims = plan_graph_gc(
            ["a", "b", "c"], set(), {"b": "2026-01-01T00:00:00"}, retain=1
        )
        assert "b" in keep
        assert set(victims) == {"a", "c"}

    def test_retain_zero_deletes_all_unreferenced(self):
        from app.services.graph_builder import plan_graph_gc

        keep, victims = plan_graph_gc(["a", "b"], {"a"}, {}, retain=0)
        assert keep == {"a"}
        assert victims == ["b"]

    def test_gc_skips_when_active_pipeline_lacks_graph_id(self, monkeypatch, tmp_path):
        """护栏：活动管线尚未写出 graph_id → 整体跳过 GC。"""
        import json
        from app.services.graph_builder import GraphBuilderService

        pdir = tmp_path / "pipelines" / "pipe_x"
        pdir.mkdir(parents=True)
        (pdir / "pipeline_state.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )
        monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(tmp_path / "pipelines"),
                            raising=False)
        monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path), raising=False)
        fake_rt = types.SimpleNamespace(
            list_graph_ids=lambda: ["g1", "g2"],
            graph_last_created_at=lambda gid: None,
        )
        monkeypatch.setattr(_rt_mod, "get_runtime", lambda: fake_rt)
        svc = GraphBuilderService.__new__(GraphBuilderService)  # 不触 Zep 构造
        audit = GraphBuilderService.gc_stale_graphs(svc, retain=0)
        assert audit["deleted"] == []
        assert "active pipeline" in (audit["skipped_reason"] or "")


# ---------------------------------------------------------------------------
# 5) UI 子图：top-K 度数排名 / min_degree / slim / 邻域 BFS（纯函数 + service 视图）
# ---------------------------------------------------------------------------
def _mk_nodes_edges():
    nodes = [
        {"uuid": f"n{i}", "name": f"Node{i}", "labels": ["Entity"],
         "summary": "s" * 10, "attributes": {"k": "v"}, "created_at": None}
        for i in range(5)
    ]
    # n0 是枢纽（度 3）；n1 度 2；n2/n3 度 1+1；n4 孤立
    edges = []
    for i, (s, t) in enumerate([("n0", "n1"), ("n0", "n2"), ("n0", "n3"), ("n1", "n2")]):
        edges.append({
            "uuid": f"e{i}", "name": "RELATES_TO", "fact": "f" * 20, "fact_type": "RELATES_TO",
            "source_node_uuid": s, "target_node_uuid": t,
            "source_node_name": s, "target_node_name": t,
            "attributes": {}, "created_at": None, "valid_at": None,
            "invalid_at": None, "expired_at": None, "episodes": ["ep1"],
        })
    return nodes, edges


class TestSubgraphFiltering:
    def test_top_k_ranks_by_degree(self):
        from app.services.graph_builder import filter_subgraph

        nodes, edges = _mk_nodes_edges()
        kept, induced = filter_subgraph(nodes, edges, top_k=2)
        kept_ids = {n["uuid"] for n in kept}
        assert kept_ids == {"n0", "n1"}          # 度数 top-2
        assert {e["uuid"] for e in induced} == {"e0"}  # 只剩两端都保留的边

    def test_min_degree_filters_isolates(self):
        from app.services.graph_builder import filter_subgraph

        nodes, edges = _mk_nodes_edges()
        kept, _ = filter_subgraph(nodes, edges, min_degree=1)
        assert {n["uuid"] for n in kept} == {"n0", "n1", "n2", "n3"}

    def test_no_params_passthrough(self):
        from app.services.graph_builder import filter_subgraph

        nodes, edges = _mk_nodes_edges()
        kept, induced = filter_subgraph(nodes, edges)
        assert len(kept) == 5 and len(induced) == 4

    def test_slim_strips_heavy_fields(self):
        from app.services.graph_builder import slim_graph_payload

        nodes, edges = _mk_nodes_edges()
        snodes, sedges = slim_graph_payload(nodes, edges)
        assert "summary" not in snodes[0] and "attributes" not in snodes[0]
        assert "fact" not in sedges[0] and "episodes" not in sedges[0]
        assert sedges[0]["source_node_uuid"] == "n0"

    def test_get_graph_data_defaults_unchanged_and_topk(self, monkeypatch, tmp_path):
        from app.services.graph_builder import GraphBuilderService, invalidate_graph_cache

        nodes, edges = _mk_nodes_edges()
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        svc = GraphBuilderService.__new__(GraphBuilderService)
        monkeypatch.setattr(
            GraphBuilderService, "_fetch_graph_raw",
            lambda self, gid, use_cache=True: {"nodes": nodes, "edges": edges},
        )
        invalidate_graph_cache("g-ui")
        full = GraphBuilderService.get_graph_data(svc, "g-ui")
        assert full["node_count"] == 5 and full["edge_count"] == 4
        assert full["truncated"] is False
        assert "summary" in full["nodes"][0]  # 缺省不 slim

        sub = GraphBuilderService.get_graph_data(svc, "g-ui", top_k=2, slim=True)
        assert sub["node_count"] == 2
        assert sub["total_node_count"] == 5 and sub["truncated"] is True
        assert "summary" not in sub["nodes"][0]

    def test_neighborhood_bfs_depth1(self, monkeypatch, tmp_path):
        from app.services.graph_builder import GraphBuilderService

        nodes, edges = _mk_nodes_edges()
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        svc = GraphBuilderService.__new__(GraphBuilderService)
        monkeypatch.setattr(
            GraphBuilderService, "_fetch_graph_raw",
            lambda self, gid, use_cache=True: {"nodes": nodes, "edges": edges},
        )
        hood = GraphBuilderService.get_node_neighborhood(svc, "g-ui", "n1", depth=1)
        ids = {n["uuid"] for n in hood["nodes"]}
        assert ids == {"n1", "n0", "n2"}
        assert hood["center_uuid"] == "n1"

    def test_ui_cache_ttl_hit_and_invalidate(self, monkeypatch):
        from app.services import graph_builder as gb

        monkeypatch.setattr(Config, "GRAPH_UI_CACHE_TTL_S", 60, raising=False)
        gb.invalidate_graph_cache("g-cache")
        calls = {"n": 0}

        def fake_nodes(client, gid):
            calls["n"] += 1
            return []

        monkeypatch.setattr(gb, "fetch_all_nodes", fake_nodes)
        monkeypatch.setattr(gb, "fetch_all_edges", lambda c, g: [])
        svc = gb.GraphBuilderService.__new__(gb.GraphBuilderService)
        svc.client = object()
        gb.GraphBuilderService._fetch_graph_raw(svc, "g-cache")
        gb.GraphBuilderService._fetch_graph_raw(svc, "g-cache")
        assert calls["n"] == 1, "TTL 内第二次读取必须命中缓存"
        gb.invalidate_graph_cache("g-cache")
        gb.GraphBuilderService._fetch_graph_raw(svc, "g-cache")
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 6) 布局：确定性 + 持久化回读
# ---------------------------------------------------------------------------
class TestLayout:
    def test_layout_deterministic_and_roundtrip(self, monkeypatch, tmp_path):
        from app.services.graph_builder import (
            compute_layout_positions, load_layout_positions, store_layout_positions,
        )

        nodes, edges = _mk_nodes_edges()
        p1 = compute_layout_positions(nodes, edges)
        p2 = compute_layout_positions(nodes, edges)
        assert set(p1) == {n["uuid"] for n in nodes}
        assert p1 == p2, "布局必须确定性（seed 固定）"
        monkeypatch.setattr(Config, "GRAPHITI_DATA_DIR", str(tmp_path), raising=False)
        store_layout_positions("g-layout", p1)
        assert load_layout_positions("g-layout") == {
            k: [float(v[0]), float(v[1])] for k, v in p1.items()
        }

    def test_layout_empty_graph(self):
        from app.services.graph_builder import compute_layout_positions

        assert compute_layout_positions([], []) == {}


# ---------------------------------------------------------------------------
# 7) 抽取加固：skip 原因分类（纯静态函数）
# ---------------------------------------------------------------------------
class TestIngestErrorClassification:
    def test_reason_buckets(self):
        from app.services.graphiti_client.runtime import GraphitiRuntime
        from graphiti_core.llm_client.errors import EmptyResponseError

        cls = GraphitiRuntime._classify_ingest_error
        assert cls(ValueError(
            "LLM returned a JSON schema instead of an instance (after temperature retries)"
        )) == "schema_echo"
        assert cls(ValueError(
            "LLM output failed ExtractedEdges validation after temperature retries: ..."
        )) == "schema_validation"
        assert cls(RuntimeError("Rate limit exceeded, 429")) == "rate_limit"
        assert cls(TimeoutError("op timeout")) == "timeout"
        assert cls(RuntimeError("boom")) == "other"
        assert cls(ValueError("LLM返回的JSON格式无效: ```json")) == "schema_parse"
        assert cls(EmptyResponseError("response was empty")) == "schema_parse"

    def test_local_json_parse_failure_uses_existing_fallback(self, monkeypatch):
        from app.services.graphiti_client.runtime import GraphitiRuntime

        runtime = GraphitiRuntime.__new__(GraphitiRuntime)
        runtime._ingest_skip_reasons = {}
        graph = object()

        async def ensure_graph(graph_id):
            assert graph_id == "g-parse"
            return graph

        in_fallback = False
        attempts = []

        async def add_episode_once(g, graph_id, **_kwargs):
            assert g is graph
            assert graph_id == "g-parse"
            attempts.append(in_fallback)
            if not in_fallback:
                raise ValueError("LLM返回的JSON格式无效: {bad")
            return "uuid-recovered"

        @_rt_mod.contextlib.asynccontextmanager
        async def fallback_swapped(graph_id, g):
            nonlocal in_fallback
            assert graph_id == "g-parse"
            assert g is graph
            in_fallback = True
            try:
                yield True
            finally:
                in_fallback = False

        monkeypatch.setattr(Config, "GRAPH_EPISODE_SCHEMA_RETRIES", 0, raising=False)
        monkeypatch.setattr(runtime, "_ensure_graph", ensure_graph)
        monkeypatch.setattr(runtime, "_add_episode_once", add_episode_once)
        monkeypatch.setattr(runtime, "_fallback_llm_swapped", fallback_swapped)

        result = asyncio.run(
            runtime._add_episode_locked(
                "g-parse",
                name="parse-failure",
                body="body",
                source_type="text",
                source_description="test",
                reference_time=None,
            )
        )

        assert result == "uuid-recovered"
        assert attempts == [False, True]
        assert runtime.pop_ingest_skip_reasons("g-parse") == {}


class TestConcurrentIngestRecovery:
    def test_rate_limit_cooldown_is_configurable_and_bounded(self, monkeypatch):
        monkeypatch.setenv("GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S", "999")
        assert _rt_mod._graph_ingest_rate_limit_cooldown_s() == 60.0
        monkeypatch.setenv("GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S", "invalid")
        assert _rt_mod._graph_ingest_rate_limit_cooldown_s() == 15.0

    def test_replays_only_rate_limit_once_and_reports_final_skips(
        self, monkeypatch
    ):
        from app.services.graphiti_client.runtime import GraphitiRuntime

        runtime = GraphitiRuntime.__new__(GraphitiRuntime)
        runtime._graphs = {}
        runtime._graph_locks = {}
        runtime._ingest_skip_reasons = {}

        async def ensure_graph(graph_id):
            assert graph_id == "g-replay"

        attempts = {
            "rate-limited": 0,
            "content-filtered": 0,
            "ok": 0,
            "persistent-rate-limit": 0,
        }
        calls = []
        replay_active = 0
        max_replay_active = 0

        async def add_episode_locked(
            graph_id, *, name, body, source_type, source_description,
            reference_time, record_skip_reason=True,
        ):
            nonlocal replay_active, max_replay_active
            assert graph_id == "g-replay"
            calls.append((name, body, record_skip_reason))
            is_replay = attempts[name] > 0
            attempts[name] += 1
            if is_replay:
                replay_active += 1
                max_replay_active = max(max_replay_active, replay_active)
                yielded = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(yielded.set_result, None)
                await yielded
            try:
                if name == "rate-limited" and attempts[name] == 1:
                    raise RuntimeError("HTTP 429 rate limit")
                if name == "persistent-rate-limit":
                    raise RuntimeError("HTTP 429 rate limit")
                if name == "content-filtered":
                    raise RuntimeError("content filtered as sensitive")
                return f"uuid-{name}"
            finally:
                if is_replay:
                    replay_active -= 1

        cooldowns = []

        async def no_sleep(delay):
            cooldowns.append(delay)

        warnings = []

        def capture_warning(message, *args):
            warnings.append(message % args)

        monkeypatch.setenv("GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S", "7.5")
        monkeypatch.setattr(runtime, "_ensure_graph", ensure_graph)
        monkeypatch.setattr(runtime, "_add_episode_locked", add_episode_locked)
        monkeypatch.setattr(_rt_mod.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(_rt_mod.logger, "warning", capture_warning)

        episodes = [
            {"name": "rate-limited", "data": "first"},
            {"name": "content-filtered", "data": "second"},
            {"name": "ok", "data": "third"},
            {"name": "persistent-rate-limit", "data": "fourth"},
        ]
        result = asyncio.run(
            runtime._add_episodes_concurrent("g-replay", episodes, concurrency=3)
        )

        assert result == ["uuid-rate-limited", "uuid-ok"]
        assert attempts == {
            "rate-limited": 2,
            "content-filtered": 1,
            "ok": 1,
            "persistent-rate-limit": 2,
        }
        assert cooldowns == [7.5]
        assert max_replay_active == 1
        assert calls[-2:] == [
            ("rate-limited", "first", False),
            ("persistent-rate-limit", "fourth", False),
        ]
        assert all(record_skip_reason is False for _, _, record_skip_reason in calls)
        assert runtime.pop_ingest_skip_reasons("g-replay") == {
            "content_filter": 1,
            "rate_limit": 1,
        }
        assert any("content_filter=1" in warning for warning in warnings)
        assert any("rate_limit=1" in warning for warning in warnings)
