"""Tests for the graph-bucket optimizations (wedge root-cause + KG quality).

Covers, with offline-safe unit checks:
- R2-KG-1/R2-KG-3: edge-fact attribute parsing + net-polarity / cumulative-lag math.
- R2-KG-4: as_of bi-temporal predicate (inert by default).
- R2-KG-5: Brandes betweenness + articulation-point chokepoints.
- GRAPH-6: zep_paging node cap resolves from Config.GRAPH_MAX_NODES.
- R2-EXEC-2/GAP-2/R2-EXEC-5: embedder in-memory + write-through disk cache (graphiti-gated).
- GAP-1/GRAPH-11: llm_adapter model_size->tier mapping + bounded inner retries (graphiti-gated).
"""

import pytest


# ---------------------------------------------------------------------------
# R2-KG-1 / R2-KG-3 / R2-KG-4: runtime causal-attribute parsing (no graphiti needed)
# ---------------------------------------------------------------------------
def test_parse_edge_attrs_and_polarity():
    from app.services.graphiti_client.runtime import (
        _parse_edge_attrs,
        _edge_polarity_sign,
        _lag_numeric,
    )

    fact = "A SUPPLIES B（sign=-，strength=high，lag=2w，polarity=-0.80）"
    a = _parse_edge_attrs(fact)
    assert a["sign"] == "-"
    assert a["strength"] == "high"
    assert a["lag"] == "2w"
    assert a["polarity"] == "-0.80"
    assert _edge_polarity_sign(a) == -1
    assert _lag_numeric(a["lag"]) == 2.0

    # missing attrs -> all None, never raises
    empty = _parse_edge_attrs("plain fact with no attrs")
    assert all(v is None for v in empty.values())
    assert _edge_polarity_sign(empty) is None
    assert _lag_numeric(empty["lag"]) is None


def test_edge_polarity_sign_token_forms():
    from app.services.graphiti_client.runtime import _edge_polarity_sign

    assert _edge_polarity_sign({"polarity": None, "sign": "+"}) == 1
    assert _edge_polarity_sign({"polarity": None, "sign": "negative"}) == -1
    assert _edge_polarity_sign({"polarity": "0.5", "sign": None}) == 1
    assert _edge_polarity_sign({"polarity": "abc", "sign": None}) is None


def test_asof_predicate_default_off():
    from app.services.graphiti_client.runtime import GraphitiRuntime

    clause, param = GraphitiRuntime._asof_predicate(None)
    assert clause == "" and param is None

    clause, param = GraphitiRuntime._asof_predicate("2026-01-01T00:00:00")
    assert "all(x IN r WHERE" in clause
    assert param == "2026-01-01T00:00:00"

    # unparseable empty string degrades to no predicate (never raises)
    clause, param = GraphitiRuntime._asof_predicate("")
    assert clause == "" and param is None


# ---------------------------------------------------------------------------
# R2-KG-5: Brandes betweenness + articulation points
# ---------------------------------------------------------------------------
def test_brandes_and_articulation_chokepoint():
    from app.services.graph_builder import _brandes_betweenness, _articulation_points

    # triangle A-B-C with a leaf D hanging off C. Removing C isolates D => C is the
    # only cut vertex and carries all the A-D / B-D shortest paths.
    adj = {"A": {"B", "C"}, "B": {"A", "C"}, "C": {"A", "B", "D"}, "D": {"C"}}
    bc = _brandes_betweenness(adj)
    assert bc["C"] == max(bc.values()) and bc["C"] > 0
    assert bc["A"] == 0.0 and bc["D"] == 0.0
    assert _articulation_points(adj) == {"C"}


def test_articulation_two_bridges_chain():
    from app.services.graph_builder import _articulation_points

    # path graph A-B-C-D: B and C are both cut vertices, endpoints are not.
    adj = {"A": {"B"}, "B": {"A", "C"}, "C": {"B", "D"}, "D": {"C"}}
    assert _articulation_points(adj) == {"B", "C"}


def test_brandes_empty_and_isolated():
    from app.services.graph_builder import _brandes_betweenness, _articulation_points

    assert _brandes_betweenness({}) == {}
    # two isolated nodes, no edges -> no betweenness, no articulation
    adj = {"X": set(), "Y": set()}
    assert _brandes_betweenness(adj) == {"X": 0.0, "Y": 0.0}
    assert _articulation_points(adj) == set()


# ---------------------------------------------------------------------------
# GRAPH-6: zep_paging node cap resolves from Config.GRAPH_MAX_NODES
# ---------------------------------------------------------------------------
def test_resolve_max_nodes_reads_config(monkeypatch):
    from app.utils import zep_paging
    from app.config import Config

    # default (attr absent) -> module default 8000
    monkeypatch.delattr(Config, "GRAPH_MAX_NODES", raising=False)
    assert zep_paging._resolve_max_nodes() == 8000

    monkeypatch.setattr(Config, "GRAPH_MAX_NODES", 12000, raising=False)
    assert zep_paging._resolve_max_nodes() == 12000

    # bad value degrades to default, never raises
    monkeypatch.setattr(Config, "GRAPH_MAX_NODES", "not-an-int", raising=False)
    assert zep_paging._resolve_max_nodes() == 8000


# ---------------------------------------------------------------------------
# R2-EXEC-2 / GAP-2 / R2-EXEC-5: embedder caching (requires graphiti_core)
# ---------------------------------------------------------------------------
def test_embedder_cache_reuse(monkeypatch, tmp_path):
    pytest.importorskip("graphiti_core")
    from app.services.graphiti_client.embedder import LocalSentenceTransformerEmbedder
    from app.config import Config

    monkeypatch.setattr(
        Config, "EMBED_DISK_CACHE_PATH", str(tmp_path / "embed_cache.sqlite"), raising=False
    )

    calls = {"n": 0, "last": None}

    def _vec(t):
        # distinct content -> distinct vector (sum of code points, not just first char)
        return [float(sum(ord(c) for c in t) % 4096)] * 4

    def fake_raw_encode(texts):
        calls["n"] += 1
        calls["last"] = list(texts)
        return [_vec(t) for t in texts]

    emb = LocalSentenceTransformerEmbedder()
    emb.embedding_dim = 4
    monkeypatch.setattr(emb, "_raw_encode", fake_raw_encode)

    # duplicate 'a' within the batch is deduped before encode; whitespace-normalized
    out = emb._encode(["a", "b", "a", "a  b"])
    assert len(out) == 4
    assert out[0] == out[2]  # same text -> same vector
    assert out[1] != out[3]  # 'b' vs 'a  b'->'a b' are distinct vectors
    assert calls["n"] == 1
    assert sorted(calls["last"]) == ["a", "a  b", "b"]  # 3 unique texts encoded once

    # second call: all served from the in-memory LRU, no new encode
    emb._encode(["a", "b"])
    assert calls["n"] == 1

    # fresh embedder, same disk path: served from the write-through sqlite store
    calls2 = {"n": 0}

    def fake_raw_encode2(texts):
        calls2["n"] += 1
        return [_vec(t) for t in texts]

    emb2 = LocalSentenceTransformerEmbedder()
    emb2.embedding_dim = 4
    monkeypatch.setattr(emb2, "_raw_encode", fake_raw_encode2)
    got = emb2._encode(["a"])
    assert calls2["n"] == 0  # disk cache hit
    assert got[0] == out[0]


def test_embedder_normalization_key(monkeypatch, tmp_path):
    pytest.importorskip("graphiti_core")
    from app.services.graphiti_client.embedder import LocalSentenceTransformerEmbedder, _normalize_text
    from app.config import Config

    monkeypatch.setattr(Config, "EMBED_DISK_CACHE_PATH", "", raising=False)  # disk off
    assert _normalize_text("  a   b ") == "a b"

    calls = {"n": 0}

    def fake_raw_encode(texts):
        calls["n"] += 1
        return [[1.0] * 4 for _ in texts]

    emb = LocalSentenceTransformerEmbedder()
    emb.embedding_dim = 4
    monkeypatch.setattr(emb, "_raw_encode", fake_raw_encode)
    emb._encode(["a b"])
    emb._encode(["a   b"])  # normalizes to same key -> cache hit, no second encode
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# GAP-1 / GRAPH-11: llm_adapter tier mapping + bounded inner retries (graphiti-gated)
# ---------------------------------------------------------------------------
def _make_adapter(app_llm):
    from app.services.graphiti_client.llm_adapter import AppGraphitiLLMClient

    return AppGraphitiLLMClient(app_llm=app_llm)


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_llm_adapter_tier_mapping(monkeypatch):
    pytest.importorskip("graphiti_core")
    import asyncio
    from graphiti_core.llm_client.config import ModelSize

    captured = {}

    class FakeApp:
        def chat_json(self, messages, temperature=0.3, max_tokens=4096, tier="strong"):
            captured["tier"] = tier
            return {"ok": True}

    adapter = _make_adapter(FakeApp())
    msgs = [_Msg("user", "extract this")]

    asyncio.run(adapter._generate_response(msgs, response_model=None, model_size=ModelSize.small))
    assert captured["tier"] == "fast"

    asyncio.run(adapter._generate_response(msgs, response_model=None, model_size=ModelSize.medium))
    assert captured["tier"] == "fast"


def test_llm_adapter_bounded_inner_retries():
    pytest.importorskip("graphiti_core")
    import asyncio
    from app.services.graphiti_client.llm_adapter import AppGraphitiLLMClient
    from graphiti_core.llm_client.errors import EmptyResponseError

    attempts = {"n": 0}

    class AlwaysSchema:
        # always echoes the schema -> exhausts inner retries
        def chat_json(self, messages, temperature=0.3, max_tokens=4096, tier="strong"):
            attempts["n"] += 1
            return {"type": "object", "properties": {}}

    adapter = AppGraphitiLLMClient(app_llm=AlwaysSchema())
    msgs = [_Msg("user", "extract")]
    with pytest.raises(EmptyResponseError):
        asyncio.run(adapter._generate_response(msgs, response_model=None))
    assert attempts["n"] == 2  # bounded to 2 attempts (GRAPH-11)


def test_llm_adapter_unwraps_schema_echo_with_real_values():
    """GRAPH-12: when the envelope's `properties` key already holds concrete values
    (not nested sub-schema descriptors), unwrap and return it instead of retrying/failing."""
    pytest.importorskip("graphiti_core")
    import asyncio
    from app.services.graphiti_client.llm_adapter import AppGraphitiLLMClient

    class SchemaEchoWithRealData:
        def chat_json(self, messages, temperature=0.3, max_tokens=4096, tier="strong"):
            return {"type": "object", "properties": {"name": "Elon Musk", "role": "CEO"}}

    adapter = AppGraphitiLLMClient(app_llm=SchemaEchoWithRealData())
    msgs = [_Msg("user", "extract")]
    result = asyncio.run(adapter._generate_response(msgs, response_model=None))
    assert result == {"name": "Elon Musk", "role": "CEO"}


def test_llm_adapter_does_not_unwrap_genuine_nested_schema():
    """A schema echo whose `properties` values are themselves schema descriptors
    (real schema, not data) must NOT be unwrapped — still exhausts retries and fails."""
    pytest.importorskip("graphiti_core")
    import asyncio
    from app.services.graphiti_client.llm_adapter import AppGraphitiLLMClient
    from graphiti_core.llm_client.errors import EmptyResponseError

    class GenuineSchemaEcho:
        def chat_json(self, messages, temperature=0.3, max_tokens=4096, tier="strong"):
            return {"type": "object", "properties": {"name": {"type": "string"}}}

    adapter = AppGraphitiLLMClient(app_llm=GenuineSchemaEcho())
    msgs = [_Msg("user", "extract")]
    with pytest.raises(EmptyResponseError):
        asyncio.run(adapter._generate_response(msgs, response_model=None))


def test_add_batch_serial_skips_failed_episode(monkeypatch):
    """RESILIENCE: in the serial ingest path, one episode that raises (e.g. a weak model
    echoing the JSON schema) is skipped+logged; the other episodes still ingest."""
    from app.config import Config
    monkeypatch.setattr(Config, "GRAPH_BUILD_CONCURRENCY", 1, raising=False)
    from app.services.graphiti_client import client as gc

    class FakeRT:
        def __init__(self):
            self.calls = 0

        def add_episode(self, graph_id, name, body, source_type,
                        source_description, reference_time):
            self.calls += 1
            if "boom" in (body or ""):
                raise ValueError("LLM returned a JSON schema instead of an instance")
            return f"uuid-{self.calls}"

    class Ep:
        def __init__(self, data):
            self.data = data
            self.type = "text"
            self.reference_time = None

    ns = gc._GraphNamespace(FakeRT())
    out = ns.add_batch("g1", [Ep("good-1"), Ep("boom"), Ep("good-2")])
    # bad episode skipped, two good ones ingested (no exception bubbles up)
    assert len(out) == 2


def test_run_timeout_cancels_and_releases_graph_lock():
    """REGRESSION: rt.run() timing out must CANCEL the coroutine so a held per-graph lock
    is released — otherwise a hung op deadlocks all later reads (observed: graph builds,
    then every post-build read times out for 15min while the DB answers in 0.01s)."""
    import asyncio
    import time
    from app.services.graphiti_client.runtime import get_runtime

    rt = get_runtime()
    gid = "lock-cancel-test"

    async def hang():
        async with rt._graph_lock(gid):
            await asyncio.sleep(60)  # would hold the lock ~forever without cancellation

    t0 = time.time()
    try:
        rt.run(hang(), timeout=0.5)
        assert False, "expected TimeoutError"
    except TimeoutError:
        pass

    async def quick():
        async with rt._graph_lock(gid):  # must be acquirable → proves the lock was released
            return "ok"

    assert rt.run(quick(), timeout=8) == "ok"
    assert time.time() - t0 < 12  # not blocked on a leaked lock
