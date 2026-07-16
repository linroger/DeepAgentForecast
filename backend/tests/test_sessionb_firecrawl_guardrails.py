"""Firecrawl 花费护栏（Session B）离线单测。

取证背景：2026-07-14 人形机器人 run 一次烧掉 ~$100 Firecrawl credit。四道护栏：

* RESEARCH_FIRECRAWL_SEARCH_LIMIT（默认 5）：/search 按条计费，实际 limit=min(max_results, 上限)；
* RESEARCH_FIRECRAWL_MAX_AGE_SECONDS（默认 172800=2 天）：/scrape 载荷带 maxAge（毫秒），
  未变页面吃 Firecrawl 端缓存不重复计费；0=不带字段；
* RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS（默认 400）：越线返回 "Error:" 哨兵、
  _resilient_fetch 顺链落 Jina 且不追加预算占用；
* RESEARCH_FIRECRAWL_MAX_SEARCH_CALLS_PER_PROCESS（默认 300）：越线返回瞬态 {"error"} JSON。

与 test_sessionb_firecrawl.py 同构：假 httpx 注入 sys.modules，零网络、零凭据。
"""

import asyncio
import json
import logging
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "deerflow_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

import cached_fetch as cf  # noqa: E402
import search_tools as st  # noqa: E402


# ---------------------------------------------------------------- helpers

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeSyncClient:
    """替身 httpx.Client：记录请求、回放预置响应。"""

    calls: list = []
    response: _FakeResponse = _FakeResponse()
    raises: "Exception | None" = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        if type(self).raises is not None:
            raise type(self).raises
        return type(self).response


class _FakeAsyncClient(_FakeSyncClient):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        return _FakeSyncClient.post(self, url, headers=headers, json=json)


@pytest.fixture()
def fake_httpx(monkeypatch):
    """把假 httpx 模块塞进 sys.modules（桥内函数体内 import httpx 时取到替身）。"""
    _FakeSyncClient.calls = []
    _FakeSyncClient.response = _FakeResponse()
    _FakeSyncClient.raises = None
    mod = types.ModuleType("httpx")
    mod.Client = _FakeSyncClient
    mod.AsyncClient = _FakeAsyncClient
    monkeypatch.setitem(sys.modules, "httpx", mod)
    return _FakeSyncClient


_GUARDRAIL_ENVS = (
    "RESEARCH_FIRECRAWL_SEARCH_LIMIT",
    "RESEARCH_FIRECRAWL_MAX_AGE_SECONDS",
    "RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS",
    "RESEARCH_FIRECRAWL_MAX_SEARCH_CALLS_PER_PROCESS",
)


@pytest.fixture(autouse=True)
def _clean_guardrail_state(monkeypatch):
    """每测重置进程内计数/告警旗标 + 清掉护栏 env（默认值路径可确定性断言）。"""
    monkeypatch.setattr(cf, "_firecrawl_fetch_calls", 0)
    monkeypatch.setattr(cf, "_firecrawl_ceiling_warned", False)
    monkeypatch.setattr(st, "_firecrawl_search_calls", 0)
    monkeypatch.setattr(st, "_firecrawl_ceiling_warned", False)
    for key in _GUARDRAIL_ENVS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")


_SCRAPE_OK = _FakeResponse(200, {
    "success": True,
    "data": {"markdown": "Humanoid shipments doubled in 2026. " * 20,
             "metadata": {"title": "Robot Report"}},
})
_SEARCH_OK = _FakeResponse(200, {
    "success": True,
    "data": {"web": [
        {"url": "https://a.example/x", "title": "A", "description": "alpha"}]},
})


# ---------------------------------------------------------------- (a) search limit 钳制

def test_search_limit_default_cap_clamps_max_results(fake_httpx):
    fake_httpx.response = _SEARCH_OK
    st._firecrawl_search("humanoid robots", 10)
    assert fake_httpx.calls[0]["json"]["limit"] == 5  # min(10, 默认 5)


def test_search_limit_env_override_and_smaller_max_results_win(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_SEARCH_LIMIT", "3")
    fake_httpx.response = _SEARCH_OK
    st._firecrawl_search("q one", 10)
    st._firecrawl_search("q two", 2)
    assert fake_httpx.calls[0]["json"]["limit"] == 3  # min(10, 3)
    assert fake_httpx.calls[1]["json"]["limit"] == 2  # min(2, 3)：更小的调用方值直接生效


def test_search_limit_invalid_env_falls_back_to_default(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_SEARCH_LIMIT", "banana")
    fake_httpx.response = _SEARCH_OK
    st._firecrawl_search("q", 10)
    assert fake_httpx.calls[0]["json"]["limit"] == 5


# ---------------------------------------------------------------- (b) scrape maxAge

def test_fetch_max_age_default_two_days_in_ms(fake_httpx):
    fake_httpx.response = _SCRAPE_OK
    asyncio.run(cf._firecrawl_fetch("https://example.com/robots"))
    assert fake_httpx.calls[0]["json"]["maxAge"] == 172_800_000  # 2 天，毫秒上送


def test_fetch_max_age_env_seconds_converted_to_ms(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_AGE_SECONDS", "3600")
    fake_httpx.response = _SCRAPE_OK
    asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert fake_httpx.calls[0]["json"]["maxAge"] == 3_600_000


def test_fetch_max_age_zero_omits_field(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_AGE_SECONDS", "0")
    fake_httpx.response = _SCRAPE_OK
    asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert "maxAge" not in fake_httpx.calls[0]["json"]


# ---------------------------------------------------------------- (c) fetch 调用上限

def test_fetch_ceiling_trips_error_sentinel_and_stops_billing(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS", "2")
    fake_httpx.response = _SCRAPE_OK
    out1 = asyncio.run(cf._firecrawl_fetch("https://example.com/1"))
    out2 = asyncio.run(cf._firecrawl_fetch("https://example.com/2"))
    out3 = asyncio.run(cf._firecrawl_fetch("https://example.com/3"))
    assert out1.startswith("# Robot Report") and out2.startswith("# Robot Report")
    assert out3 == "Error: Firecrawl per-run call ceiling reached (2)"
    assert not cf._is_cacheable(out3)  # "Error:" 哨兵：不落缓存 → 链条顺落 Jina
    assert len(fake_httpx.calls) == 2  # 越线后不再触网（不再计费）


def test_fetch_ceiling_resilient_chain_falls_through_to_jina(monkeypatch):
    """越线后 _resilient_fetch 完全跳过 Firecrawl（零请求、零追加预算），Jina 接管。"""
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS", "1")
    monkeypatch.setattr(cf, "_firecrawl_fetch_calls", 1)  # 已用完上限
    monkeypatch.setattr(cf, "_research_budget", None)
    order = []

    async def fake_firecrawl(url):  # pragma: no cover - must not run
        order.append("firecrawl")
        return "unreachable"

    async def fake_jina(url):
        order.append("jina")
        return "# Page\n\n" + "usable body content " * 30

    monkeypatch.setattr(cf, "_firecrawl_fetch", fake_firecrawl)
    monkeypatch.setattr(cf, "_jina_delegate_fetch", fake_jina)
    out = asyncio.run(cf._resilient_fetch("https://example.com"))
    assert order == ["jina"]
    assert out.startswith("# Page")


def test_fetch_ceiling_sentinel_is_last_resort_when_all_providers_fail(monkeypatch):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS", "1")
    monkeypatch.setattr(cf, "_firecrawl_fetch_calls", 1)
    monkeypatch.setattr(cf, "_research_budget", None)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    async def fake_jina(url):
        return ""  # 空结果：不可缓存 → 继续兜底

    monkeypatch.setattr(cf, "_jina_delegate_fetch", fake_jina)
    out = asyncio.run(cf._resilient_fetch("https://example.com"))
    assert out == "Error: Firecrawl per-run call ceiling reached (1)"


def test_fetch_ceiling_warns_exactly_once(monkeypatch, fake_httpx, caplog):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS", "1")
    monkeypatch.setattr(cf, "_firecrawl_fetch_calls", 1)
    with caplog.at_level(logging.WARNING, logger="cached_fetch"):
        asyncio.run(cf._firecrawl_fetch("https://example.com/a"))
        asyncio.run(cf._firecrawl_fetch("https://example.com/b"))
    warnings = [r for r in caplog.records
                if r.name == "cached_fetch" and "上限" in r.getMessage()]
    assert len(warnings) == 1


def test_fetch_ceiling_zero_means_unlimited(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_FETCH_CALLS_PER_PROCESS", "0")
    monkeypatch.setattr(cf, "_firecrawl_fetch_calls", 10_000)
    fake_httpx.response = _SCRAPE_OK
    out = asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert out.startswith("# Robot Report")


# ---------------------------------------------------------------- (c) search 调用上限

def test_search_ceiling_trips_transient_error_shape(monkeypatch, fake_httpx):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_SEARCH_CALLS_PER_PROCESS", "1")
    fake_httpx.response = _SEARCH_OK
    first = json.loads(st._firecrawl_search("humanoid robots", 5))
    second_raw = st._firecrawl_search("humanoid robots follow-up", 5)
    second = json.loads(second_raw)
    assert first["total_results"] == 1
    assert second["error"] == "firecrawl search per-run call ceiling reached (1)"
    assert second["query"] == "humanoid robots follow-up"
    # 瞬态 error 形状：不算真实空结果（不负缓存）、不可正缓存 → 调用方仍可回退/重试。
    assert not st._is_search_no_result(second_raw)
    assert not st._is_search_cacheable(second_raw)
    assert len(fake_httpx.calls) == 1  # 越线后不再触网（不再计费）


def test_search_ceiling_warns_exactly_once(monkeypatch, fake_httpx, caplog):
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_SEARCH_CALLS_PER_PROCESS", "1")
    monkeypatch.setattr(st, "_firecrawl_search_calls", 1)
    with caplog.at_level(logging.WARNING, logger="search_tools"):
        st._firecrawl_search("q one", 5)
        st._firecrawl_search("q two", 5)
    warnings = [r for r in caplog.records
                if r.name == "search_tools" and "上限" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------- (d) 计数遥测

def test_fetch_counter_counts_billed_calls_including_http_errors(fake_httpx):
    fake_httpx.response = _SCRAPE_OK
    asyncio.run(cf._firecrawl_fetch("https://example.com/1"))
    fake_httpx.response = _FakeResponse(429, {})  # 已发出的请求同样计数（可能已计费）
    asyncio.run(cf._firecrawl_fetch("https://example.com/2"))
    assert cf.get_firecrawl_call_counts() == {"fetch": 2}


def test_search_counter_counts_billed_calls(fake_httpx):
    fake_httpx.response = _SEARCH_OK
    st._firecrawl_search("q one", 5)
    st._firecrawl_search("q two", 5)
    assert st.get_firecrawl_call_counts() == {"search": 2}


def test_counters_do_not_count_keyless_or_over_ceiling_paths(monkeypatch, fake_httpx):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    asyncio.run(cf._firecrawl_fetch("https://example.com"))  # 无 key：不触网不计数
    assert cf.get_firecrawl_call_counts() == {"fetch": 0}
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("RESEARCH_FIRECRAWL_MAX_SEARCH_CALLS_PER_PROCESS", "1")
    monkeypatch.setattr(st, "_firecrawl_search_calls", 1)
    st._firecrawl_search("q", 5)  # 越线：不触网不再递增
    assert st.get_firecrawl_call_counts() == {"search": 1}
    assert fake_httpx.calls == []
