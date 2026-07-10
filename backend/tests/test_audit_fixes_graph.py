"""Audit fixes（graph 组）回归测试：KG-3/KG-5/KG-7/KG-8/KG-11/ONT-7/XRUN-6。

只测本组改动的可观测行为；不触网、不起真实图谱后端（全部 stub/monkeypatch）。
"""

import contextlib
import importlib.util
import json
import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Config  # noqa: E402
from app.services import graph_builder as gb_mod  # noqa: E402
from app.services.graph_builder import (  # noqa: E402
    GraphBuilderService,
    fold_priors_with_aliases,
)
from app.services.zep_entity_reader import ZepEntityReader  # noqa: E402


# ---------------------------------------------------------------------------
# KG-7: fold_priors_with_aliases
# ---------------------------------------------------------------------------

class TestFoldPriorsWithAliases:
    def test_curated_alias_group_folds_to_group_max(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", True, raising=False)
        priors = {"TSM": 0.0256, "TSMC": 0.3333, "OpenAI": 0.6}
        actors = {"actors": [{"name": "TSMC", "aliases": ["TSM", "台积电"]}]}
        out = fold_priors_with_aliases(priors, actors)
        assert out["TSM"] == pytest.approx(0.3333)
        assert out["TSMC"] == pytest.approx(0.3333)
        # 缺键的组员补键（消费者按别名查表不落空）
        assert out["台积电"] == pytest.approx(0.3333)
        # 组外键不受影响
        assert out["OpenAI"] == pytest.approx(0.6)

    def test_values_never_decrease(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", True, raising=False)
        priors = {"A": 0.9, "B": 0.1}
        actors = {"actors": [{"name": "B", "aliases": ["A"]}]}
        out = fold_priors_with_aliases(priors, actors)
        assert out["A"] == pytest.approx(0.9)  # 高的不降
        assert out["B"] == pytest.approx(0.9)  # 低的抬升到组 MAX

    def test_no_heuristic_containment_folding(self, monkeypatch):
        # 修订版要求：只认策展别名组；"IEEPA" vs "IEEPA-based tariffs" 这类包含对不折叠。
        monkeypatch.setattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", True, raising=False)
        priors = {"IEEPA": 0.1282, "IEEPA-based tariffs": 0.1026}
        actors = {"actors": [{"name": "IEEPA"}, {"name": "IEEPA-based tariffs"}]}
        out = fold_priors_with_aliases(priors, actors)
        assert out == priors

    def test_flag_off_returns_unmodified_copy(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", False, raising=False)
        priors = {"TSM": 0.02, "TSMC": 0.33}
        actors = {"actors": [{"name": "TSMC", "aliases": ["TSM"]}]}
        out = fold_priors_with_aliases(priors, actors)
        assert out == priors
        assert out is not priors  # 永远返回拷贝

    def test_degrade_safe_on_garbage_inputs(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_PRIORS_ALIAS_FOLD", True, raising=False)
        assert fold_priors_with_aliases(None, None) == {}
        assert fold_priors_with_aliases({}, {"actors": "not-a-list"}) == {}
        priors = {"X": 0.5}
        assert fold_priors_with_aliases(priors, {"actors": [{"name": "Y", "aliases": [1, None]}]}) == priors


# ---------------------------------------------------------------------------
# 共用 stub：节点/边对象 + shell GraphBuilderService
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _capture_warnings(logger_name="mirofish.graph_builder"):
    """mirofish 日志树不向 root 传播（caplog 收不到），直接挂临时 handler 捕获。"""
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _H(level=logging.WARNING)
    lg = logging.getLogger(logger_name)
    lg.addHandler(h)
    try:
        yield records
    finally:
        lg.removeHandler(h)


def _node(uuid, name, labels=None):
    return types.SimpleNamespace(uuid_=uuid, name=name, labels=labels or ["Entity"],
                                 summary="", attributes={})


def _edge(src, tgt):
    return types.SimpleNamespace(source_node_uuid=src, target_node_uuid=tgt)


def _shell_builder():
    b = GraphBuilderService.__new__(GraphBuilderService)
    b.client = types.SimpleNamespace(graph=types.SimpleNamespace())
    b.task_manager = None
    b.last_ingest_stats = None
    return b


# ---------------------------------------------------------------------------
# KG-5: add_text_batches 落库记账
# ---------------------------------------------------------------------------

class TestIngestStats:
    def _run(self, monkeypatch, add_batch):
        monkeypatch.setattr(Config, "GRAPHITI_REMOTE", False, raising=False)
        b = _shell_builder()
        b.client.graph.add_batch = add_batch
        return b

    def test_partial_failure_recorded(self, monkeypatch):
        calls = {"n": 0}

        def add_batch(graph_id, episodes):
            calls["n"] += 1
            if calls["n"] == 2:  # 第二批整批失败
                raise RuntimeError("422 content filter")
            return [types.SimpleNamespace(uuid_=f"ep{calls['n']}-{i}")
                    for i in range(len(episodes))]

        b = self._run(monkeypatch, add_batch)
        uuids = b.add_text_batches("g1", ["c1", "c2", "c3", "c4", "c5", "c6"], batch_size=2)
        assert len(uuids) == 4
        # Wave9-KG 起 last_ingest_stats 额外携带 skip_ratio/skip_reasons（ADD-only），
        # 这里断言原三键语义不变 + 新增 skip_ratio 正确。
        stats = b.last_ingest_stats
        assert (stats["total"], stats["failed"], stats["succeeded"]) == (6, 2, 4)
        assert stats["skip_ratio"] == pytest.approx(2 / 6, abs=1e-4)

    def test_total_failure_still_raises_and_records(self, monkeypatch):
        def add_batch(graph_id, episodes):
            raise RuntimeError("all down")

        b = self._run(monkeypatch, add_batch)
        with pytest.raises(ValueError):
            b.add_text_batches("g1", ["c1", "c2"], batch_size=2)
        # 记账发生在硬护栏 raise 之前，失败路径也可观测（Wave9-KG 起含 skip_ratio 等新键）
        stats = b.last_ingest_stats
        assert (stats["total"], stats["failed"], stats["succeeded"]) == (2, 2, 0)
        assert stats["skip_ratio"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# KG-8 + KG-3: _get_graph_info 的欠合并告警 + chokepoint 上限读 Config
# ---------------------------------------------------------------------------

class TestGraphInfoIntegrity:
    def _info(self, monkeypatch, nodes, edges):
        monkeypatch.setattr(gb_mod, "fetch_all_nodes", lambda c, g: nodes)
        monkeypatch.setattr(gb_mod, "fetch_all_edges", lambda c, g: edges)
        return _shell_builder()._get_graph_info("g1")

    def test_under_merge_warning_fires_with_float_ratio(self, monkeypatch):
        # 10 节点 1 边 → 9 分量；ratio=0.5 → 9 > 5.0 触发（旧 int 截断口径同样触发，
        # 这里锁定浮点语义 + 告警附带最大分量枢纽名）。
        monkeypatch.setattr(Config, "GRAPH_COMPONENT_WARN_RATIO", 0.5, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_PRIORS", False, raising=False)
        nodes = [_node(f"n{i}", f"Name{i}") for i in range(10)]
        edges = [_edge("n0", "n1")]
        with _capture_warnings() as records:
            gi = self._info(monkeypatch, nodes, edges)
        assert gi.components == 9
        warn = [r for r in records if "UNDER-MERGED" in r.getMessage()]
        assert warn, "欠合并告警必须触发"
        # 最大分量（n0-n1）的枢纽名出现在告警里
        assert "Name0" in warn[0].getMessage() or "Name1" in warn[0].getMessage()

    def test_ratio_lowered_fires_on_cited_shape(self, monkeypatch):
        # 审计引用形态：132 节点 30 分量在 ratio=0.2 下必须告警（30 > 26.4）。
        monkeypatch.setattr(Config, "GRAPH_COMPONENT_WARN_RATIO", 0.2, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_PRIORS", False, raising=False)
        nodes = [_node(f"n{i}", f"Name{i}") for i in range(132)]
        # 前 103 个节点连成一个链式分量（102 条边），其余 29 个孤点 → 共 30 分量
        edges = [_edge(f"n{i}", f"n{i+1}") for i in range(102)]
        with _capture_warnings() as records:
            gi = self._info(monkeypatch, nodes, edges)
        assert gi.components == 30
        assert any("UNDER-MERGED" in r.getMessage() for r in records)

    def test_single_component_never_warns(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_COMPONENT_WARN_RATIO", 0.2, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_PRIORS", False, raising=False)
        nodes = [_node("a", "A"), _node("b", "B")]
        edges = [_edge("a", "b")]
        with _capture_warnings() as records:
            self._info(monkeypatch, nodes, edges)
        assert not any("UNDER-MERGED" in r.getMessage() for r in records)

    def test_chokepoint_cap_read_from_config(self, monkeypatch):
        # KG-3: GRAPH_CHOKEPOINT_MAX_NODES 从 Config 读；cap 小于节点数 → 跳过计算。
        monkeypatch.setattr(Config, "GRAPH_COMPONENT_WARN_RATIO", 0.99, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_PRIORS", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_MAX_NODES", 2, raising=False)
        nodes = [_node(f"n{i}", f"N{i}") for i in range(4)]
        edges = [_edge("n0", "n1"), _edge("n1", "n2"), _edge("n2", "n3")]
        gi = self._info(monkeypatch, nodes, edges)
        assert not gi.betweenness and not gi.chokepoints  # 超上限被跳过

        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_MAX_NODES", 100, raising=False)
        gi = self._info(monkeypatch, nodes, edges)
        assert gi.betweenness  # 上限放宽后正常计算（链中点介数最高）
        assert "N1" in gi.chokepoints and "N2" in gi.chokepoints

    def test_chokepoint_cap_garbage_falls_back(self, monkeypatch):
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_PRIORS", True, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHOKEPOINT_MAX_NODES", "not-a-number", raising=False)
        nodes = [_node("a", "A"), _node("b", "B"), _node("c", "C")]
        edges = [_edge("a", "b"), _edge("b", "c")]
        gi = self._info(monkeypatch, nodes, edges)  # 兜底 1500，不抛异常
        assert gi.chokepoints and gi.chokepoints[0] == "B"  # 链中点介数最高、排首位


# ---------------------------------------------------------------------------
# KG-11 + ONT-7: zep_entity_reader
# ---------------------------------------------------------------------------

def _reader(nodes, edges):
    r = ZepEntityReader.__new__(ZepEntityReader)
    r.get_all_nodes = lambda gid: [dict(n) for n in nodes]
    r.get_all_edges = lambda gid: [dict(e) for e in edges]
    return r


def _rnode(uuid, name, labels):
    return {"uuid": uuid, "name": name, "labels": labels, "summary": "", "attributes": {}}


def _redge(uuid, name, src, tgt):
    return {"uuid": uuid, "name": name, "fact": f"{src}->{tgt}",
            "source_node_uuid": src, "target_node_uuid": tgt, "attributes": {}}


class TestEdgeIndexEquivalence:
    def test_related_edges_preserve_full_scan_order(self, monkeypatch):
        monkeypatch.setattr(Config, "SIM_TIER_ELIGIBILITY", True, raising=False)
        nodes = [_rnode("a", "A", ["Entity", "Person"]),
                 _rnode("b", "B", ["Entity", "Organization"]),
                 _rnode("c", "C", ["Entity"])]
        # 交错顺序：incoming(e1) → outgoing(e2) → 自环(e3, 只记 outgoing) → outgoing(e4)
        edges = [_redge("e1", "R1", "b", "a"),
                 _redge("e2", "R2", "a", "b"),
                 _redge("e3", "SELF", "a", "a"),
                 _redge("e4", "R4", "a", "c")]
        result = _reader(nodes, edges).filter_defined_entities("g1", enrich_with_edges=True)
        ent_a = next(e for e in result.entities if e.name == "A")
        assert [(re["direction"], re["edge_name"]) for re in ent_a.related_edges] == [
            ("incoming", "R1"), ("outgoing", "R2"), ("outgoing", "SELF"), ("outgoing", "R4"),
        ]
        # 关联节点：b（双向）、a（自环）、c
        rel_uuids = {n["uuid"] for n in ent_a.related_nodes}
        assert rel_uuids == {"a", "b", "c"}
        # 无自定义标签的 c 仍被过滤掉（口径不变）
        assert all(e.name != "C" for e in result.entities)


class TestOntologyTierGate:
    ONTOLOGY = {"entity_types": [
        {"name": "MarketSignal", "archetype": "signal"},
        {"name": "Person"},  # 无显式分类 → 不产生信号
    ]}

    def _nodes(self):
        return [_rnode("m1", "TSMC Q2 print", ["Entity", "MarketSignal"]),
                _rnode("p1", "Jensen Huang", ["Entity", "Person"])]

    def test_flag_off_ontology_ignored(self, monkeypatch):
        monkeypatch.setattr(Config, "SIM_TIER_ELIGIBILITY", True, raising=False)
        monkeypatch.setattr(Config, "SIM_TIER_FROM_ONTOLOGY", False, raising=False)
        result = _reader(self._nodes(), []).filter_defined_entities(
            "g1", enrich_with_edges=False, ontology=self.ONTOLOGY)
        assert {e.name for e in result.entities} == {"TSMC Q2 print", "Jensen Huang"}

    def test_flag_on_type_signal_demotes_instances(self, monkeypatch):
        monkeypatch.setattr(Config, "SIM_TIER_ELIGIBILITY", True, raising=False)
        monkeypatch.setattr(Config, "SIM_TIER_FROM_ONTOLOGY", True, raising=False)
        result = _reader(self._nodes(), []).filter_defined_entities(
            "g1", enrich_with_edges=False, ontology=self.ONTOLOGY)
        # MarketSignal(archetype=signal) → tier 3 → 出池；Person 无类型信号 → 保留
        assert {e.name for e in result.entities} == {"Jensen Huang"}

    def test_node_level_signal_takes_precedence(self, monkeypatch):
        monkeypatch.setattr(Config, "SIM_TIER_ELIGIBILITY", True, raising=False)
        monkeypatch.setattr(Config, "SIM_TIER_FROM_ONTOLOGY", True, raising=False)
        nodes = self._nodes()
        # 节点级显式 tier=1 覆盖类型级 signal 分类 → 保留
        nodes[0]["attributes"] = {"simulation_tier": 1}
        result = _reader(nodes, []).filter_defined_entities(
            "g1", enrich_with_edges=False, ontology=self.ONTOLOGY)
        assert {e.name for e in result.entities} == {"TSMC Q2 print", "Jensen Huang"}


# ---------------------------------------------------------------------------
# XRUN-6: 死信回放 CLI
# ---------------------------------------------------------------------------

def _load_replay_module():
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "scripts", "replay_zep_dead_letters.py"))
    spec = importlib.util.spec_from_file_location("replay_zep_dead_letters", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_dead_letter(path, records, extra_raw_lines=()):
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for ln in extra_raw_lines:
            fh.write(ln + "\n")


def _dl_record(graph_id, text):
    return {"graph_id": graph_id, "platform": "twitter", "combined_text": text,
            "agent_name": "a", "action_type": "CREATE_POST", "round": 1,
            "timestamp": "t", "dead_lettered_at": "t"}


class TestDeadLetterReplay:
    def _client(self, fail_after=None):
        sent = []

        def add(graph_id, type="text", data=""):
            if fail_after is not None and len(sent) >= fail_after:
                raise RuntimeError("backend down")
            sent.append((graph_id, data))

        client = types.SimpleNamespace(graph=types.SimpleNamespace(add=add))
        return client, sent

    def test_full_replay_archives_file(self, tmp_path, monkeypatch):
        mod = _load_replay_module()
        monkeypatch.setattr(mod, "SEND_INTERVAL", 0)
        p = str(tmp_path / "mirofish_test1.jsonl")
        _write_dead_letter(p, [_dl_record("mirofish_test1", f"post {i}") for i in range(3)])
        client, sent = self._client()
        st = mod.replay_file(client, p, batch_size=2, dry_run=False)
        assert st["replayed"] == 3 and st["remaining"] == 0 and not st["error"]
        assert len(sent) == 2 and all(g == "mirofish_test1" for g, _ in sent)
        assert not os.path.exists(p)  # 已归档改名
        assert any(f.startswith("mirofish_test1.jsonl.replayed-") for f in os.listdir(tmp_path))

    def test_failure_keeps_remaining_records(self, tmp_path, monkeypatch):
        mod = _load_replay_module()
        monkeypatch.setattr(mod, "SEND_INTERVAL", 0)
        p = str(tmp_path / "mirofish_test2.jsonl")
        _write_dead_letter(p, [_dl_record("mirofish_test2", f"post {i}") for i in range(4)],
                           extra_raw_lines=["{not json"])
        client, _ = self._client(fail_after=1)  # 第一批成功，第二批失败
        st = mod.replay_file(client, p, batch_size=2, dry_run=False)
        assert st["replayed"] == 2 and st["error"]
        # 剩余 2 条有效记录 + 1 条坏行原样写回，可续跑
        with open(p, encoding="utf-8") as fh:
            remaining = [ln for ln in fh.read().splitlines() if ln.strip()]
        assert len(remaining) == 3 and "{not json" in remaining

    def test_dry_run_touches_nothing(self, tmp_path):
        mod = _load_replay_module()
        p = str(tmp_path / "mirofish_test3.jsonl")
        _write_dead_letter(p, [_dl_record("mirofish_test3", "post")])
        client, sent = self._client()
        st = mod.replay_file(client, p, batch_size=5, dry_run=True)
        assert st["remaining"] == 1 and not sent
        assert os.path.exists(p)
