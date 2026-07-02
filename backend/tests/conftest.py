"""Shared pytest fixtures (EXECPLAN2 I-7-0 / I-7-4).

Offline-first: no test here may hit a real LLM or network. The FakeLLMClient
fixture lets generator/report code be exercised deterministically without
burning API calls (I-7-4: record/replay-style stub).
"""

import os
import sys

import pytest

# Make `import app...` work when running pytest from the backend/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeLLMClient:
    """Drop-in stand-in for app.utils.llm_client.LLMClient.

    Returns scripted responses in order (or a default), and records every call
    so tests can assert what was sent. Mirrors the real surface: chat(),
    chat_json(), supports_native_tools().
    """

    def __init__(self, responses=None, json_responses=None, provider="fake", model="fake-1"):
        self.provider = provider
        self.model = model
        self._responses = list(responses or [])
        self._json_responses = list(json_responses or [])
        self.calls = []

    def chat(self, messages, temperature=0.7, max_tokens=4096, response_format=None):
        self.calls.append({"kind": "chat", "messages": messages, "temperature": temperature})
        if self._responses:
            return self._responses.pop(0)
        return "FAKE_RESPONSE"

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        self.calls.append({"kind": "chat_json", "messages": messages, "temperature": temperature})
        if self._json_responses:
            return self._json_responses.pop(0)
        return {}

    def supports_native_tools(self):
        return False


@pytest.fixture(autouse=True)
def _no_prediction_market_network(monkeypatch):
    """预测市场客户端离线化（offline-first 契约）：开发机 .env 的真实 ODDPOOL_API_KEY
    会被 Config 的 load_dotenv 带进测试进程，任何走到 OddpoolClient 兜底现抓的路径都可能
    发真实请求。测试一律摘除 key → client.enabled=False → 快速降级为空结果。
    需要 client 行为的测试显式传 api_key= 并 mock httpx（见 test_prediction_markets.py）。"""
    monkeypatch.delenv("ODDPOOL_API_KEY", raising=False)
    try:
        from app.config import Config
        if getattr(Config, "ODDPOOL_API_KEY", None):
            monkeypatch.setattr(Config, "ODDPOOL_API_KEY", "", raising=False)
    except Exception:  # noqa: BLE001 — Config 不可导入时无 key 可摘
        pass


@pytest.fixture
def fake_llm():
    """Factory: fake_llm(responses=[...], json_responses=[...]) -> FakeLLMClient."""
    def _make(**kwargs):
        return FakeLLMClient(**kwargs)
    return _make


@pytest.fixture
def tmp_run_dir(tmp_path):
    """A throwaway run directory for artifact-write tests."""
    d = tmp_path / "run"
    d.mkdir()
    return str(d)
