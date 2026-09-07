"""Offline SDK boundaries retain physical attempts independently of response validity."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils import llm_client as lc
from app.utils import telemetry as tel
from app.utils.usage_ledger import UsageLedgerStorageError


@pytest.fixture
def bound(tmp_path, monkeypatch):
    rid, path = "pipe-physical", tmp_path / "usage.sqlite3"
    previous = tel.get_run_context()
    tel.LLMMeter.reset(rid)
    tel.LLMMeter.attach_durable_run(rid, str(path), attempt_id="parent-attempt")
    tel.set_run_context(rid, "report")
    for name, value in {"LLM_TELEMETRY_ENABLED": True, "LLM_CACHE_ENABLED": False,
                        "LLM_TIERED_ROUTING": False, "LLM_RUN_BUDGET_TOKENS": 0,
                        "LLM_RUN_BUDGET_USD": 0}.items():
        monkeypatch.setattr(Config, name, value)
    for name in ("LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lc, "_CB_STATE", {})
    monkeypatch.setattr(lc, "_FB_OPENAI_CLIENTS", {})
    monkeypatch.setattr(lc, "_FB_AUTH_UNAVAILABLE_UNTIL", {})
    monkeypatch.setattr(lc, "_retry_delay", lambda *_: 0)
    monkeypatch.setattr(tel.LLMCache, "_store", {})
    monkeypatch.setattr(tel.LLMCache, "_order", [])
    yield rid, path
    tel.LLMMeter.reset(rid)
    tel.set_run_context(*previous)


def response(content="ok", inputs=10, outputs=5, *, usage="default", tools=None):
    if usage == "default":
        usage = SimpleNamespace(prompt_tokens=inputs, completion_tokens=outputs,
                                total_tokens=inputs + outputs)
    return SimpleNamespace(usage=usage, choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=content, tool_calls=tools or []))])


def client(create, provider="minimax"):
    instance = lc.LLMClient(provider="claude-cli", model="fixture")
    instance.provider = provider
    instance._openai_client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    return instance


def operations(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM usage_operations WHERE source='llm_api_attempt' ORDER BY rowid").fetchall()
    return [{**dict(row), "counter": json.loads(row["counter_json"]),
             "metadata": json.loads(row["metadata_json"])} for row in rows]


def test_empty_response_then_success_retains_both_attempts_before_validation(bound):
    rid, path = bound
    replies = iter([response("", 11, 7), response("ok", 13, 5)])
    seen = []

    def create(**kwargs):
        rows = operations(path)
        assert rows[-1]["status"] == "in_flight"
        assert rows[-1]["counter"]["calls"] == 0
        assert rows[-1]["counter"]["total_tokens"] == 0
        seen.append(rows[-1]["operation_id"])
        return next(replies)

    assert client(create).chat([]) == "ok"
    total = tel.LLMMeter.cumulative_snapshot(rid)["total"]
    assert (total["calls"], total["prompt_tokens"], total["completion_tokens"]) == (2, 24, 12)
    assert len(set(seen)) == 2
    assert all(row["status"] == "completed" for row in operations(path))


def test_transport_failures_keep_unknown_attempts(bound):
    rid, path = bound

    def fail(**kwargs):
        raise RuntimeError("offline transport interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        client(fail).chat([])
    rows = operations(path)
    assert len(rows) == lc.MAX_RETRIES
    assert all(row["status"] == "unknown" and row["metadata"]["usage_class"] == "unknown" for row in rows)
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["calls"] == lc.MAX_RETRIES


def test_all_empty_responses_keep_reported_usage(bound):
    rid, path = bound
    with pytest.raises(RuntimeError, match="空 content"):
        client(lambda **k: response("", 10, 5)).chat([])
    assert len(operations(path)) == lc.MAX_RETRIES
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 15 * lc.MAX_RETRIES


def test_primary_failure_and_api_fallback_have_distinct_owners(bound, monkeypatch):
    rid, path = bound

    def fail(**kwargs):
        raise ValueError("offline primary rejection")

    fallback = client(lambda **k: response("fallback", 20, 4), "openai")
    monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *a: fallback._openai_client))
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    assert client(fail).chat([]) == "fallback"
    rows = operations(path)
    assert [row["metadata"]["provider"] for row in rows] == ["minimax", "openai"]
    assert [row["status"] for row in rows] == ["unknown", "completed"]
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 24


def test_json_repair_counts_each_physical_response_once(bound):
    rid, path = bound
    replies = iter([response("invalid", 8, 4), response('{"ok": true}', 9, 5)])
    assert client(lambda **k: next(replies)).chat_json([]) == {"ok": True}
    assert len(operations(path)) == 2
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 26


def test_native_tool_only_response_and_invalid_choices_are_metered(bound):
    rid, path = bound
    tool = SimpleNamespace(id="call-a", function=SimpleNamespace(name="search", arguments='{"q":"x"}'))
    api = client(lambda **k: response(None, 8, 4, tools=[tool]))
    assert api.chat_with_tools([], [])["tool_calls"] == [{"id": "call-a", "name": "search", "arguments": {"q": "x"}}]
    bad = response("unused", 9, 5)
    bad.choices = []
    api._openai_client.chat.completions.create = lambda **k: bad
    with pytest.raises(IndexError):
        api.chat_with_tools([], [])
    assert len(operations(path)) == 2
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 26


def test_missing_usage_never_reuses_empty_attempt_usage(bound):
    rid, path = bound
    replies = iter([response("", 10, 4), response("x" * 20, usage=None)])
    api = client(lambda **k: next(replies))
    assert api.chat([{"role": "user", "content": "abcd"}]) == "x" * 20
    assert api._last_usage is None
    rows = operations(path)
    assert [row["metadata"]["usage_class"] for row in rows] == ["known", "estimated"]
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 20


@pytest.mark.parametrize("usage", [
    "malformed", [], False,
    {"prompt_tokens": True, "completion_tokens": 1},
    {"prompt_tokens": "10", "completion_tokens": 1},
    {"prompt_tokens": -1, "completion_tokens": 1},
    {"prompt_tokens": 1.5, "completion_tokens": 1},
    {"prompt_tokens": float("nan"), "completion_tokens": 1},
    {"prompt_tokens": 10},
    {"total_tokens": 100},
    {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 100},
    {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": "malformed"},
    {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": []},
    {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 20}},
    {"prompt_tokens": 10, "completion_tokens": 1, "cache_creation_input_tokens": 20},
    {"cache_read_input_tokens": 100},
    {"prompt_tokens": 0, "completion_tokens": None, "prompt_tokens_details": {"cached_tokens": 20}},
])
@pytest.mark.parametrize("native", [False, True])
def test_invalid_usage_stops_without_retry_and_retains_unknown_operation(bound, usage, native):
    _rid, path = bound
    seen = []
    api = client(lambda **k: seen.append(True) or response(usage=usage))
    with pytest.raises(UsageLedgerStorageError):
        api.chat_with_tools([], []) if native else api.chat([])
    with pytest.raises(UsageLedgerStorageError):
        api.chat([])
    assert seen == [True]
    rows = operations(path)
    assert len(rows) == 1 and rows[0]["status"] == "accounting_error"


def test_cache_hit_creates_no_physical_operation_and_clears_last_usage(bound, monkeypatch):
    _rid, path = bound
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", True)
    seen = []
    api = client(lambda **k: seen.append(True) or response())
    assert api.chat([]) == "ok"
    assert api._last_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert api.chat([]) == "ok"
    assert api._last_usage is None
    assert seen == [True] and len(operations(path)) == 1


def test_shared_client_keeps_thread_usage_separate(bound):
    rid, path = bound
    barrier = threading.Barrier(2)

    def create(**kwargs):
        count = int(kwargs["messages"][0]["content"])
        barrier.wait(timeout=5)
        return response(str(count), count, 1)

    api = client(create)

    def invoke(count):
        tel.set_run_context(rid, "report")
        result = api.chat([{"role": "user", "content": str(count)}])
        barrier.wait(timeout=5)
        return result, api._last_usage

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(invoke, [10, 100]))
    assert outputs == [("10", {"prompt_tokens": 10, "completion_tokens": 1}),
                       ("100", {"prompt_tokens": 100, "completion_tokens": 1})]
    assert len(operations(path)) == 2
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 112


@pytest.mark.parametrize("native", [False, True])
def test_fast_route_owns_usage_cache_options_and_circuit_state(bound, monkeypatch, native):
    rid, path = bound
    for name, value in {"LLM_TIERED_ROUTING": True, "LLM_FAST_PROVIDER": "kimi",
                        "LLM_FAST_API_KEY": "offline", "LLM_FAST_BASE_URL": "https://offline.invalid",
                        "LLM_CACHE_ENABLED": not native}.items():
        monkeypatch.setattr(Config, name, value)
    monkeypatch.setattr(Config, "fast_model", classmethod(lambda cls: "fixture"))
    monkeypatch.setattr(Config, "strong_model", classmethod(lambda cls: "fixture"))
    monkeypatch.setattr(Config, "reasoning_extra_body", classmethod(
        lambda cls, provider: {"thinking": {"type": "disabled"}} if provider == "kimi" else None))
    main, fast = [], []
    api = client(lambda **k: main.append(k) or response("primary"))
    usage = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
             "prompt_tokens_details": {"cached_tokens": 8}, "cache_creation_input_tokens": 2}
    api._fast_openai_client = client(lambda **k: fast.append(k) or response("fast", usage=usage))._openai_client
    lc._CB_STATE["kimi"] = {"consec": 2.0}
    lc._CB_STATE["minimax"] = {"consec": 3.0}
    if native:
        assert api.chat_with_tools([], [], tier="fast")["content"] == "fast"
    else:
        assert api.chat([], tier="fast") == "fast"
        assert api.chat([], tier="fast") == "fast"  # cache hit
    assert len(fast) == 1 and main == []
    assert fast[0]["temperature"] == 0.6
    assert fast[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert lc._CB_STATE["kimi"]["consec"] == 0
    assert lc._CB_STATE["minimax"]["consec"] == 3
    rows = operations(path)
    assert rows[0]["metadata"]["provider"] == "kimi"
    assert rows[0]["counter"]["cost_usd"] == tel.estimate_cost("kimi", 20, 5)
    assert rows[0]["counter"]["cache_read_tokens"] == 8
    assert rows[0]["counter"]["cache_write_tokens"] == 2
    assert rows[0]["counter"]["total_tokens"] == 25
    assert tel.LLMMeter.cumulative_snapshot(rid)["cache_partition_known"] is False
    if not native:
        # Identical model/messages under the primary provider cannot reuse the
        # fast provider's cached response.
        assert api.chat([], tier="strong") == "primary"
        assert len(main) == 1 and len(operations(path)) == 2


@pytest.mark.parametrize("native", [False, True])
def test_fast_failure_updates_only_actual_provider_breaker(bound, monkeypatch, native):
    _rid, path = bound
    monkeypatch.setattr(Config, "LLM_TIERED_ROUTING", True)
    monkeypatch.setattr(Config, "LLM_FAST_PROVIDER", "kimi")
    api = client(lambda **k: pytest.fail("primary dispatch must not run"))

    def fail(**kwargs):
        raise ValueError("Error code: 422 - new_sensitive")

    monkeypatch.setattr(api, "_fast_provider_client", lambda: client(fail)._openai_client)
    with pytest.raises(ValueError):
        api.chat_with_tools([], [], tier="fast") if native else api.chat([], tier="fast")
    assert lc._CB_STATE["kimi"]["consec"] == 1
    assert "minimax" not in lc._CB_STATE
    assert operations(path)[0]["metadata"]["provider"] == "kimi"


def test_fallback_clears_stale_primary_receipt_and_budget_stop_escapes(bound, monkeypatch):
    rid, path = bound
    monkeypatch.setattr(lc, "MAX_RETRIES", 1)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    replies = []
    fallback = client(lambda **k: replies.append(True) or response("fallback", 20, 4))._openai_client
    monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *a: fallback))
    api = client(lambda **k: response("", 2, 1))
    assert api.chat([]) == "fallback"
    assert api._last_usage is None
    assert len(operations(path)) == 2
    assert tel.LLMMeter.cumulative_snapshot(rid)["total"]["total_tokens"] == 27
    monkeypatch.setattr(Config, "LLM_RUN_BUDGET_TOKENS", 30)
    with pytest.raises(tel.BudgetExceeded):
        api._try_fallback([], 0.3, 10, None, RuntimeError("primary down"))
    with pytest.raises(tel.BudgetExceeded):
        api.chat([])
    assert replies == [True]  # Remaining allowance rejects fallback before dispatch.


def test_predispatch_storage_failure_creates_no_operation_or_provider_call(bound, monkeypatch):
    _rid, path = bound
    monkeypatch.setattr(tel.LLMMeter, "record_snapshot", classmethod(
        lambda *a, **k: (_ for _ in ()).throw(UsageLedgerStorageError("offline marker write failed"))))
    api = client(lambda **k: pytest.fail("storage failure must prevent dispatch"))
    with pytest.raises(UsageLedgerStorageError):
        api.chat([])
    assert operations(path) == []


def test_fallback_response_is_cached_only_under_serving_provider(bound, monkeypatch):
    _rid, path = bound
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", True)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback-model")
    fallback = client(lambda **k: response("fallback"))._openai_client
    monkeypatch.setattr(lc.LLMClient, "_build_openai_client", staticmethod(lambda *a: fallback))

    def fail(**kwargs):
        raise ValueError("offline primary rejection")

    assert client(fail).chat([]) == "fallback"
    assert tel.LLMCache.get(tel.LLMCache.key("minimax", "fixture", [], 0.7, 4096, None)) is None
    assert tel.LLMCache.get(tel.LLMCache.key("openai", "fallback-model", [], 0.7, 4096, None)) == "fallback"
    assert len(operations(path)) == 2
