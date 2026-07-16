"""Firecrawl provider（web_fetch 主抓 + web_search 后端）离线单测。

与 test_bridge_search_and_cache.py 同构：桥模块模块级只 import 标准库，把 deerflow_bridge
挂到 sys.path 后直接 import 做纯逻辑测试。全部 monkeypatch 注入假 httpx，零网络、零凭据。

取证背景（2026-07-14 人形机器人 run，handoff.md）：匿名 Jina 抓取 53% ConnectTimeout、
社区 DDG 搜索 78% 空结果。Firecrawl 配 key 后成为 fetch 主抓与 search 实际后端。
"""

import asyncio
import json
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


# ---------------------------------------------------------------- 后端调度

def _clear_search_keys(monkeypatch):
    for key in ("SERPER_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_select_provider_firecrawl_when_only_its_key_set(monkeypatch):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    assert st._select_search_provider() == "firecrawl"


def test_select_provider_serper_beats_firecrawl(monkeypatch):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("SERPER_API_KEY", "sk-serper")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    assert st._select_search_provider() == "serper"


def test_select_provider_ddg_when_firecrawl_key_blank(monkeypatch):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "   ")
    assert st._select_search_provider() == "ddg"


# ---------------------------------------------------------------- search 直连实现

def test_firecrawl_search_maps_v2_web_rows(monkeypatch, fake_httpx):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fake_httpx.response = _FakeResponse(200, {
        "success": True,
        "data": {"web": [
            {"url": "https://a.example/x", "title": "A", "description": "alpha"},
            {"url": "https://b.example/y", "title": "", "description": "beta"},
            {"notaurl": True},
        ]},
    })
    out = json.loads(st._firecrawl_search("humanoid robots", 5))
    assert out["total_results"] == 2
    assert out["results"][0] == {
        "title": "A", "url": "https://a.example/x", "content": "alpha"}
    # 空 title 回退为 URL；无 url 的行被丢弃。
    assert out["results"][1]["title"] == "https://b.example/y"
    body = fake_httpx.calls[0]
    assert body["json"] == {"query": "humanoid robots", "limit": 5,
                            "sources": ["web"]}
    assert body["headers"]["Authorization"] == "Bearer fc-test"


def test_firecrawl_search_empty_is_no_result_not_error(monkeypatch, fake_httpx):
    """真实空结果 → results=[]（可负缓存）；不是 error 形状（error 不负缓存）。"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fake_httpx.response = _FakeResponse(200, {"success": True, "data": {"web": []}})
    out = st._firecrawl_search("no coverage topic", 10)
    assert st._is_search_no_result(out)
    assert not st._is_search_cacheable(out)
    assert "error" not in json.loads(out)


def test_firecrawl_search_http_error_is_transient_error_shape(monkeypatch, fake_httpx):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fake_httpx.response = _FakeResponse(500, {})
    out = json.loads(st._firecrawl_search("q", 10))
    assert "HTTP 500" in out["error"]
    assert not st._is_search_no_result(json.dumps(out))


def test_firecrawl_search_exception_never_raises_or_leaks(monkeypatch, fake_httpx):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-do-not-leak")
    fake_httpx.raises = RuntimeError("boom with Bearer fc-secret-do-not-leak")
    out = st._firecrawl_search("q", 10)
    assert "fc-secret-do-not-leak" not in out
    assert json.loads(out)["error"].startswith("firecrawl search failed: RuntimeError")


def test_web_search_impl_routes_to_firecrawl(monkeypatch, fake_httpx):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", "0")  # 绕过正缓存
    monkeypatch.setattr(st, "_research_budget", None)
    fake_httpx.response = _FakeResponse(200, {"data": {"web": [
        {"url": "https://a.example", "title": "A", "description": "alpha"}]}})
    out = json.loads(st.web_search_impl("firecrawl routing probe", 3))
    assert out["total_results"] == 1
    assert fake_httpx.calls, "firecrawl 后端必须被实际调用"


def test_web_search_impl_falls_back_to_ddg_without_httpx(monkeypatch):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(st, "_firecrawl_search_available", lambda: False)
    monkeypatch.setattr(st, "_research_budget", None)
    monkeypatch.setattr(st, "_load_search_module", lambda provider: None)
    out = json.loads(st.web_search_impl("q", 3))
    assert out["error"] == "no web_search backend available"


# ---------------------------------------------------------------- fetch 主抓

def test_firecrawl_fetch_returns_titled_markdown(monkeypatch, fake_httpx):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fake_httpx.response = _FakeResponse(200, {
        "success": True,
        "data": {"markdown": "Humanoid shipments doubled in 2026. " * 20,
                 "metadata": {"title": "Robot Report"}},
    })
    out = asyncio.run(cf._firecrawl_fetch("https://example.com/robots"))
    assert out.startswith("# Robot Report\n\n")
    assert "shipments doubled" in out
    body = fake_httpx.calls[0]["json"]
    # Session B 花费护栏后，/scrape 载荷默认携带 maxAge（毫秒；缺省 2 天=172800000）。
    assert body == {"url": "https://example.com/robots",
                    "formats": ["markdown"], "onlyMainContent": True,
                    "maxAge": 172_800_000}


def test_firecrawl_fetch_error_paths(monkeypatch, fake_httpx):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    fake_httpx.response = _FakeResponse(429, {})
    out = asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert out.startswith("Error:") and cf._is_transport_failure(out)
    fake_httpx.response = _FakeResponse(200, {"data": {"markdown": ""}})
    out = asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert out == "Error: Firecrawl returned no page text"
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    out = asyncio.run(cf._firecrawl_fetch("https://example.com"))
    assert "FIRECRAWL_API_KEY is not configured" in out


def test_resilient_fetch_firecrawl_leads_and_jina_falls_back(monkeypatch):
    """key 已配 → firecrawl 为主抓；其失败后 Jina 需额外预算并可接管。"""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    order = []

    async def fake_firecrawl(url):
        order.append("firecrawl")
        return "Error: Firecrawl failed: ConnectTimeout"

    async def fake_jina(url):
        order.append("jina")
        return "# Page\n\n" + "usable body content " * 30

    monkeypatch.setattr(cf, "_firecrawl_fetch", fake_firecrawl)
    monkeypatch.setattr(cf, "_jina_delegate_fetch", fake_jina)
    monkeypatch.setattr(cf, "_research_budget", None)
    out = asyncio.run(cf._resilient_fetch("https://example.com"))
    assert order == ["firecrawl", "jina"]
    assert out.startswith("# Page")


def test_resilient_fetch_firecrawl_success_short_circuits(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    called = []

    async def fake_firecrawl(url):
        called.append("firecrawl")
        return "# Doc\n\n" + "content body " * 40

    async def fake_jina(url):  # pragma: no cover - must not run
        called.append("jina")
        return "unreachable"

    monkeypatch.setattr(cf, "_firecrawl_fetch", fake_firecrawl)
    monkeypatch.setattr(cf, "_jina_delegate_fetch", fake_jina)
    monkeypatch.setattr(cf, "_research_budget", None)

    async def _run():
        # _FETCH_PROVIDER 是 contextvar：asyncio.run 的写入不会传回外层 context，
        # 须在同一协程内读取（与生产读取点 cached_fetch 同 task 的语义一致）。
        result = await cf._resilient_fetch("https://example.com")
        return result, cf._FETCH_PROVIDER.get()

    out, provider = asyncio.run(_run())
    assert called == ["firecrawl"]
    assert provider == "firecrawl"
    assert out.startswith("# Doc")


def test_resilient_fetch_without_key_is_jina_first_unchanged(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    order = []

    async def fake_jina(url):
        order.append("jina")
        return "# Page\n\n" + "usable body content " * 30

    monkeypatch.setattr(cf, "_jina_delegate_fetch", fake_jina)
    monkeypatch.setattr(cf, "_research_budget", None)
    out = asyncio.run(cf._resilient_fetch("https://example.com"))
    assert order == ["jina"]
    assert out.startswith("# Page")


# ---------------------------------------------------------------- 凭据卫生

def test_no_hardcoded_firecrawl_key_in_bridge_sources():
    """真实 key 只允许存在于 gitignored .env，绝不进源码。"""
    for name in ("cached_fetch.py", "search_tools.py"):
        text = open(os.path.join(_BRIDGE_DIR, name), encoding="utf-8").read()
        assert "fc-4934" not in text, f"{name} 内不得出现真实 Firecrawl key"
