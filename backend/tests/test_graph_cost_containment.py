"""Graph-stage cost containment regression tests (W10-COST).

Forensic evidence (2026-07 runs): the graph stage burned 8h37m (62% of a
13h58m run). 278/466 dossier chunks (60%) were skipped only AFTER full retry
ladders, and retry stacking produced 23,496 rate-limit/connection-error log
lines in one day. These tests pin the two containment levers:

1. GRAPH_CAST_CHUNK_FILTER — chunks that mention no seeded cast actor are
   skipped BEFORE any LLM call, counted separately (skipped_cast_filter) so
   the GRAPH_MAX_SKIPPED_RATIO alarm's denominator only sees LLM-submitted
   chunks.
2. GRAPH_CHUNK_MAX_ATTEMPTS — per-chunk TOTAL full-extraction attempts across
   the episode ladder + provider fallback + batch replay are capped (default
   2); a breaker-tripped provider (llm_client i2 fail-fast) propagates as a
   fast, bounded skip instead of re-entering graph-side ladders.

Plus the slim graph payload carrying valid_at/invalid_at (frontend temporal
dimming). No network, no graph backend: everything is stubbed.
"""

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Config  # noqa: E402
from app.services.graph_builder import (  # noqa: E402
    GraphBuilderService,
    cast_filter_terms_from_actors,
    chunk_mentions_cast,
    slim_graph_payload,
)
from app.services.graphiti_client.runtime import GraphitiRuntime  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ACTORS = {
    "as_of_date": "2026-08-01",
    "actors": [
        {"name": "Elon Musk", "type": "Person", "aliases": ["马斯克"]},
        {"name": "Tesla", "type": "Organization", "aliases": ["特斯拉"]},
    ],
    "relationships": [
        {"source": "Elon Musk", "target": "Tesla", "type": "OTHER",
         "relation_label": "LEADS", "basis": "CEO of Tesla"},
    ],
}

CAST_CHUNK_EN = "Elon Musk unveiled the new roadmap at the shareholder meeting."
CAST_CHUNK_ZH = "特斯拉发布了新车型，市场反应热烈。"
NOCAST_CHUNK = "The weather in Paris was mild and calm throughout the week."


def _fake_runtime_stub(monkeypatch, skip_reasons=None):
    """Neutralize the real process-global runtime used for skip accounting."""
    from app.services.graphiti_client import runtime as rt_mod

    stub = types.SimpleNamespace(
        pop_ingest_skip_reasons=lambda gid: dict(skip_reasons or {})
    )
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: stub)
    return stub


def _builder(add_batch):
    b = GraphBuilderService.__new__(GraphBuilderService)
    b.client = types.SimpleNamespace(
        graph=types.SimpleNamespace(
            add_batch=add_batch,
            add_triplet=lambda *a, **k: "uuid-seed",
        )
    )
    b.task_manager = None
    b.last_ingest_stats = None
    b.last_actor_graph_seed_manifest = None
    return b


def _capturing_add_batch(sent, fail_batches=()):
    calls = {"n": 0}

    def add_batch(graph_id, episodes):
        calls["n"] += 1
        if calls["n"] in fail_batches:
            raise RuntimeError("422 content filter")
        batch = [getattr(e, "data", "") for e in episodes]
        sent.append(batch)
        return [types.SimpleNamespace(uuid_=f"u{calls['n']}-{i}")
                for i in range(len(episodes))]

    return add_batch


def _bare_runtime():
    rt = GraphitiRuntime.__new__(GraphitiRuntime)
    rt._ingest_skip_reasons = {}
    rt._graphs = {}
    rt._graph_locks = {}
    rt._fb_depth = {}
    rt._fb_primary = {}
    rt._fallback_llm = None
    return rt


def _fake_graph():
    return types.SimpleNamespace(
        llm_client="primary",
        clients=types.SimpleNamespace(llm_client="primary"),
        tracer=None,
    )


class _Msg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


BREAKER_MSG = ("LLM 调用失败：主提供方 minimax 处于 422/429 熔断冷却，"
               "且回退提供方不可用")


# ---------------------------------------------------------------------------
# Config defaults (containment knobs must default ON / bounded)
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_cast_chunk_filter_defaults_on(self):
        assert Config.GRAPH_CAST_CHUNK_FILTER is True

    def test_chunk_max_attempts_defaults_to_two(self):
        assert Config.GRAPH_CHUNK_MAX_ATTEMPTS == 2


# ---------------------------------------------------------------------------
# Task 1: cast-relevance chunk pre-filter
# ---------------------------------------------------------------------------

class TestCastTermExtraction:
    def test_terms_cover_names_aliases_and_relationship_endpoints(self):
        terms = cast_filter_terms_from_actors(ACTORS)
        assert "elonmusk" in terms
        assert "tesla" in terms
        assert "马斯克" in terms
        assert "特斯拉" in terms

    def test_single_char_terms_excluded(self):
        assert cast_filter_terms_from_actors({"actors": [{"name": "X"}]}) == []

    def test_garbage_inputs_yield_no_terms(self):
        assert cast_filter_terms_from_actors(None) == []
        assert cast_filter_terms_from_actors({"actors": "nope"}) == []


class TestChunkMentionsCast:
    TERMS = ["elonmusk", "tesla", "马斯克", "特斯拉"]

    def test_case_and_spacing_insensitive(self):
        assert chunk_mentions_cast("ELON  MUSK spoke today", self.TERMS)

    def test_cjk_alias_match(self):
        assert chunk_mentions_cast("马斯克今天发布了声明", self.TERMS)

    def test_no_mention_is_false(self):
        assert not chunk_mentions_cast(NOCAST_CHUNK, self.TERMS)

    def test_empty_terms_pass_through(self):
        assert chunk_mentions_cast(NOCAST_CHUNK, [])


class TestCastChunkFilter:
    def _seeded(self, monkeypatch, add_batch, actors=ACTORS):
        _fake_runtime_stub(monkeypatch)
        monkeypatch.setattr(Config, "GRAPHITI_REMOTE", False, raising=False)
        b = _builder(add_batch)
        b.seed_actors("g-filter", actors)
        return b

    def test_no_cast_chunks_skipped_without_llm_call(self, monkeypatch):
        sent = []
        b = self._seeded(monkeypatch, _capturing_add_batch(sent))
        uuids = b.add_text_batches(
            "g-filter", [CAST_CHUNK_EN, NOCAST_CHUNK, CAST_CHUNK_ZH], batch_size=2
        )
        submitted = [c for batch in sent for c in batch]
        assert submitted == [CAST_CHUNK_EN, CAST_CHUNK_ZH]
        assert len(uuids) == 2
        stats = b.last_ingest_stats
        # Alarm denominator (total) counts only LLM-submitted chunks.
        assert (stats["total"], stats["failed"], stats["succeeded"]) == (2, 0, 2)
        assert stats["skip_ratio"] == pytest.approx(0.0)
        assert stats["input_chunks"] == 3
        assert stats["skipped_cast_filter"] == 1
        # Distinct label in the existing skip-reason accounting.
        assert stats["skip_reasons"]["skipped_cast_filter"] == 1

    def test_filter_disabled_restores_old_behavior(self, monkeypatch):
        sent = []
        b = self._seeded(monkeypatch, _capturing_add_batch(sent))
        monkeypatch.setattr(Config, "GRAPH_CAST_CHUNK_FILTER", False, raising=False)
        b.add_text_batches(
            "g-filter", [CAST_CHUNK_EN, NOCAST_CHUNK, CAST_CHUNK_ZH], batch_size=2
        )
        submitted = [c for batch in sent for c in batch]
        assert submitted == [CAST_CHUNK_EN, NOCAST_CHUNK, CAST_CHUNK_ZH]
        stats = b.last_ingest_stats
        assert stats["total"] == 3
        assert stats["skipped_cast_filter"] == 0
        assert "skipped_cast_filter" not in stats["skip_reasons"]

    def test_all_filtered_bypasses_filter(self, monkeypatch):
        # An all-filtered corpus smells like a term/script mismatch — submit
        # everything rather than silently building a seed-only graph.
        sent = []
        b = self._seeded(monkeypatch, _capturing_add_batch(sent))
        b.add_text_batches("g-filter", [NOCAST_CHUNK, NOCAST_CHUNK + " again"],
                           batch_size=2)
        submitted = [c for batch in sent for c in batch]
        assert len(submitted) == 2
        stats = b.last_ingest_stats
        assert stats["total"] == 2
        assert stats["skipped_cast_filter"] == 0

    def test_unseeded_graph_is_never_filtered(self, monkeypatch):
        _fake_runtime_stub(monkeypatch)
        monkeypatch.setattr(Config, "GRAPHITI_REMOTE", False, raising=False)
        sent = []
        b = _builder(_capturing_add_batch(sent))  # no seed_actors call
        b.add_text_batches("g-noseed", [CAST_CHUNK_EN, NOCAST_CHUNK], batch_size=2)
        submitted = [c for batch in sent for c in batch]
        assert submitted == [CAST_CHUNK_EN, NOCAST_CHUNK]
        assert b.last_ingest_stats["skipped_cast_filter"] == 0

    def test_alarm_denominator_excludes_filtered_chunks(self, monkeypatch):
        # 2 cast chunks submitted (one batch fails) + 1 filtered chunk:
        # skip_ratio must be llm-failures / submitted, NOT (failures+filtered)/input.
        sent = []
        b = self._seeded(monkeypatch, _capturing_add_batch(sent, fail_batches={2}))
        b.add_text_batches(
            "g-filter", [CAST_CHUNK_EN, NOCAST_CHUNK, CAST_CHUNK_ZH], batch_size=1
        )
        stats = b.last_ingest_stats
        assert (stats["total"], stats["failed"]) == (2, 1)
        assert stats["skip_ratio"] == pytest.approx(0.5)
        assert stats["input_chunks"] == 3
        assert stats["skipped_cast_filter"] == 1


# ---------------------------------------------------------------------------
# Task 2: per-chunk total attempt cap (GRAPH_CHUNK_MAX_ATTEMPTS)
# ---------------------------------------------------------------------------

class TestChunkAttemptCap:
    def _wire(self, monkeypatch, rt, g, exc_factory, calls):
        async def ensure(graph_id):
            return g

        async def once(g_, graph_id, **kw):
            calls.append(rt._fb_depth.get(graph_id, 0) > 0)
            raise exc_factory()

        monkeypatch.setattr(rt, "_ensure_graph", ensure)
        monkeypatch.setattr(rt, "_add_episode_once", once)

    def _run_locked(self, rt, graph_id="g-cap"):
        return asyncio.run(rt._add_episode_locked(
            graph_id, name="c0", body="b", source_type="text",
            source_description="t", reference_time=None,
        ))

    def test_schema_failure_capped_at_two_with_fallback_final_slot(self, monkeypatch):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = []
        self._wire(monkeypatch, rt, g, lambda: ValueError(
            "LLM returned a JSON schema instead of an instance"), calls)
        fb = types.SimpleNamespace(set_tracer=lambda t: None)
        monkeypatch.setattr(rt, "_get_fallback_llm", lambda: fb)
        monkeypatch.setattr(Config, "GRAPH_EPISODE_SCHEMA_RETRIES", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 2, raising=False)
        with pytest.raises(ValueError) as ei:
            self._run_locked(rt)
        # cap=2: one primary attempt, final budgeted slot goes to the fallback
        assert calls == [False, True]
        assert getattr(ei.value, "_graphiti_ingest_attempts", None) == 2
        assert rt.pop_ingest_skip_reasons("g-cap") == {"fallback_schema_echo": 1}

    def test_schema_failure_capped_without_fallback(self, monkeypatch):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = []
        self._wire(monkeypatch, rt, g, lambda: ValueError(
            "LLM returned a JSON schema instead of an instance"), calls)
        monkeypatch.setattr(rt, "_get_fallback_llm", lambda: None)
        monkeypatch.setattr(Config, "GRAPH_EPISODE_SCHEMA_RETRIES", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 2, raising=False)
        with pytest.raises(ValueError):
            self._run_locked(rt)
        assert calls == [False, False]  # 2 primary attempts, nothing more
        assert rt.pop_ingest_skip_reasons("g-cap") == {"schema_echo": 1}

    def test_uncapped_legacy_topology_preserved(self, monkeypatch):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = []
        self._wire(monkeypatch, rt, g, lambda: ValueError(
            "LLM returned a JSON schema instead of an instance"), calls)
        fb = types.SimpleNamespace(set_tracer=lambda t: None)
        monkeypatch.setattr(rt, "_get_fallback_llm", lambda: fb)
        monkeypatch.setattr(Config, "GRAPH_EPISODE_SCHEMA_RETRIES", 1, raising=False)
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 0, raising=False)
        with pytest.raises(ValueError):
            self._run_locked(rt)
        # <=0 disables the cap: 2 primary attempts + 1 fallback (today's ladder)
        assert calls == [False, False, True]

    def test_rate_limit_never_burns_the_episode_ladder(self, monkeypatch):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = []
        self._wire(monkeypatch, rt, g,
                   lambda: RuntimeError("HTTP 429 rate limit"), calls)
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 2, raising=False)
        with pytest.raises(RuntimeError) as ei:
            self._run_locked(rt)
        assert calls == [False]
        assert getattr(ei.value, "_graphiti_ingest_attempts", None) == 1


# ---------------------------------------------------------------------------
# Task 2 (i2 interplay): breaker-tripped provider → fast, bounded skip
# ---------------------------------------------------------------------------

class TestBreakerFailFastPropagation:
    def test_adapter_does_not_retry_breaker_fail_fast(self):
        pytest.importorskip("graphiti_core")
        from app.services.graphiti_client.llm_adapter import AppGraphitiLLMClient
        from graphiti_core.llm_client.client import is_server_or_retry_error

        calls = {"n": 0}

        class BreakerTripped:
            def chat_json(self, messages, temperature=0.3, max_tokens=4096,
                          tier="strong"):
                calls["n"] += 1
                raise RuntimeError(BREAKER_MSG)

        adapter = AppGraphitiLLMClient(app_llm=BreakerTripped())
        with pytest.raises(RuntimeError) as ei:
            asyncio.run(adapter._generate_response([_Msg("user", "extract")]))
        # i2 fail-fast passes straight through: no temperature-ladder retries,
        # and graphiti's tenacity predicate refuses to retry it.
        assert calls["n"] == 1
        assert not is_server_or_retry_error(ei.value)

    def test_breaker_tripped_chunk_bounded_by_cap_in_concurrent_path(
        self, monkeypatch
    ):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = {"n": 0}

        async def ensure(graph_id):
            return g

        async def once(g_, graph_id, **kw):
            calls["n"] += 1
            raise RuntimeError(BREAKER_MSG)

        monkeypatch.setattr(rt, "_ensure_graph", ensure)
        monkeypatch.setattr(rt, "_add_episode_once", once)
        monkeypatch.setenv("GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S", "0")
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 2, raising=False)

        out = asyncio.run(
            rt._add_episodes_concurrent("g-brk", [{"name": "c0", "data": "x"}], 2)
        )
        assert out == []
        # fan-out attempt + single batch replay == cap; no ladder stacking
        assert calls["n"] == 2
        assert rt.pop_ingest_skip_reasons("g-brk") == {"rate_limit": 1}

    def test_cap_one_disables_batch_replay(self, monkeypatch):
        rt = _bare_runtime()
        g = _fake_graph()
        calls = {"n": 0}

        async def ensure(graph_id):
            return g

        async def once(g_, graph_id, **kw):
            calls["n"] += 1
            raise RuntimeError(BREAKER_MSG)

        monkeypatch.setattr(rt, "_ensure_graph", ensure)
        monkeypatch.setattr(rt, "_add_episode_once", once)
        monkeypatch.setenv("GRAPH_INGEST_RATE_LIMIT_COOLDOWN_S", "0")
        monkeypatch.setattr(Config, "GRAPH_CHUNK_MAX_ATTEMPTS", 1, raising=False)

        out = asyncio.run(
            rt._add_episodes_concurrent("g-brk1", [{"name": "c0", "data": "x"}], 2)
        )
        assert out == []
        assert calls["n"] == 1
        assert rt.pop_ingest_skip_reasons("g-brk1") == {"rate_limit": 1}


# ---------------------------------------------------------------------------
# Task 4: slim payload keeps bi-temporal edge fields
# ---------------------------------------------------------------------------

class TestSlimPayloadTemporalKeys:
    def test_slim_edges_carry_valid_and_invalid_at(self):
        nodes = [{"uuid": "a", "name": "A", "labels": ["Entity"], "summary": "s"}]
        edges = [{
            "uuid": "e1", "name": "R", "fact_type": "R",
            "source_node_uuid": "a", "target_node_uuid": "b",
            "source_node_name": "A", "target_node_name": "B",
            "fact": "heavy", "episodes": ["ep"], "summary": "heavy",
            "valid_at": "2026-01-01T00:00:00", "invalid_at": None,
        }]
        _snodes, sedges = slim_graph_payload(nodes, edges)
        assert sedges[0]["valid_at"] == "2026-01-01T00:00:00"
        assert "invalid_at" in sedges[0]
        # heavy fields stay stripped
        assert "fact" not in sedges[0] and "episodes" not in sedges[0]
