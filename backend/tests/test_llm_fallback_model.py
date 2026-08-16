"""TASK 2/3 (audit): fallback model-name inheritance + dual-outage fail-fast.

Observed failure mode (2026-07-08): with primary=minimax (MiniMax-M2) and an
openai-compatible fallback gateway, an unset LLM_FALLBACK_MODEL made the
fallback client inherit Config.LLM_MODEL_NAME — the PRIMARY provider's model —
so every failover deterministically failed 400 "unknown provider for model
MiniMax-M2". Unlike 401s (900s cooldown) those 400s retried forever, and with
the primary breaker tripped each chat() still ground through the full
3-attempt primary retry loop + a second doomed fallback (up to 231 errors/min
for 26 hours).

All tests are offline: LLMClient.chat / transports are monkeypatched.
"""

import time

import pytest

import app.utils.llm_client as lc


@pytest.fixture(autouse=True)
def _clean_llm_module_state(monkeypatch):
    """These module-level caches are process-global; isolate every test."""
    for env in ("LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL",
                "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    lc._FB_AUTH_UNAVAILABLE_UNTIL.clear()
    lc._FB_OPENAI_CLIENTS.clear()
    lc._CB_STATE.clear()
    getattr(lc, "_FB_MISCONFIG_WARNED", set()).clear()
    yield
    lc._FB_AUTH_UNAVAILABLE_UNTIL.clear()
    lc._FB_OPENAI_CLIENTS.clear()
    lc._CB_STATE.clear()
    getattr(lc, "_FB_MISCONFIG_WARNED", set()).clear()


# ---------------------------------------------------------------------------
# TASK 2a: unset LLM_FALLBACK_MODEL + different openai-compatible provider →
# refuse to build the fallback client instead of inheriting the primary model.
# ---------------------------------------------------------------------------

def test_unset_fallback_model_refuses_openai_compat_fallback(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "http://127.0.0.1:1/v1")
    # LLM_FALLBACK_MODEL deliberately unset → old code inherited the PRIMARY
    # provider's Config.LLM_MODEL_NAME into the fallback client.

    constructed = []
    monkeypatch.setattr(
        lc.LLMClient, "_build_openai_client",
        staticmethod(lambda provider, api_key, base_url:
                     constructed.append((provider, api_key, base_url)) or object()),
    )
    chat_calls = []
    monkeypatch.setattr(
        lc.LLMClient, "chat",
        lambda self, *a, **k: chat_calls.append(self.model) or "should-not-happen",
    )

    primary = lc.LLMClient(provider="claude-cli")
    out = primary._try_fallback(
        [{"role": "user", "content": "q"}], 0.3, 64, None,
        RuntimeError("primary down"))

    assert out is None
    assert constructed == [], (
        "fallback client must not be constructed with an inherited primary model")
    assert chat_calls == []


def test_cli_fallback_without_model_still_allowed(monkeypatch):
    """CLI fallbacks (claude-cli) carry no wire model name — must keep working."""
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "claude-cli")
    calls = {"n": 0}

    def fake_chat(self, *_a, **_k):
        calls["n"] += 1
        return "cli-fb-answer"

    monkeypatch.setattr(lc.LLMClient, "chat", fake_chat)
    primary = lc.LLMClient(provider="minimax", api_key="sk-test",
                           base_url="http://127.0.0.1:1/v1", model="MiniMax-M3")
    out = primary._try_fallback(
        [{"role": "user", "content": "q"}], 0.3, 64, None,
        RuntimeError("primary down"))
    assert out == "cli-fb-answer"
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# TASK 2b: deterministic 400 (invalid model class) enters the same
# process-scoped cooldown as deterministic 401s.
# ---------------------------------------------------------------------------

def test_invalid_model_400_enters_process_cooldown(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "MiniMax-M2")  # wrong model for gateway
    calls = {"n": 0}

    def fail_400(self, *_a, **_k):
        calls["n"] += 1
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'unknown provider for model "
            "MiniMax-M2', 'type': 'invalid_request_error'}}")

    monkeypatch.setattr(lc.LLMClient, "chat", fail_400)
    primary = lc.LLMClient(provider="claude-cli")
    args = ([{"role": "user", "content": "q"}], 0.3, 64, None,
            RuntimeError("primary down"))
    assert primary._try_fallback(*args) is None
    assert primary._try_fallback(*args) is None
    assert calls["n"] == 1, (
        "second failover must skip the deterministically-doomed 400 fallback")


def test_transient_fallback_failure_does_not_enter_cooldown(monkeypatch):
    """A retryable failure (e.g. timeout) must NOT trip the deterministic cooldown."""
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "MiniMax-M3")
    calls = {"n": 0}

    def fail_timeout(self, *_a, **_k):
        calls["n"] += 1
        raise RuntimeError("Request timed out")

    monkeypatch.setattr(lc.LLMClient, "chat", fail_timeout)
    primary = lc.LLMClient(provider="claude-cli")
    args = ([{"role": "user", "content": "q"}], 0.3, 64, None,
            RuntimeError("primary down"))
    assert primary._try_fallback(*args) is None
    assert primary._try_fallback(*args) is None
    assert calls["n"] == 2  # transient errors keep getting a fresh chance


# ---------------------------------------------------------------------------
# TASK 3: primary breaker tripped + fallback unavailable → chat() raises
# immediately (no 3-attempt primary grind, no backoff sleeps, no 2nd fallback).
# ---------------------------------------------------------------------------

def test_dual_outage_fails_fast_without_primary_retry_loop(monkeypatch):
    lc._CB_STATE["minimax"] = {
        "consec": 0.0, "consec429": 0.0,
        "tripped_until": time.monotonic() + 300.0,
    }
    primary_calls = {"n": 0}

    def primary_transport(self, *_a, **_k):
        primary_calls["n"] += 1
        raise RuntimeError("primary transport must not run during breaker cooldown")

    monkeypatch.setattr(lc.LLMClient, "_chat_openai", primary_transport)
    fallback_calls = {"n": 0}

    def fallback_none(self, *_a, **_k):
        fallback_calls["n"] += 1
        return None

    monkeypatch.setattr(lc.LLMClient, "_try_fallback", fallback_none)
    sleeps = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))

    client = lc.LLMClient(provider="minimax", api_key="sk-test",
                          base_url="http://127.0.0.1:1/v1", model="MiniMax-M3")
    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "q"}])

    assert primary_calls["n"] == 0, "no doomed primary attempts during dual outage"
    assert fallback_calls["n"] == 1, "exactly one fallback attempt, no second pass"
    assert sleeps == [], "no exponential-backoff grind during dual outage"


def test_untripped_breaker_keeps_normal_retry_path(monkeypatch):
    """Guard: when the breaker is NOT tripped, chat() behavior is unchanged
    (primary retry loop still runs, then the fallback is consulted once)."""
    primary_calls = {"n": 0}

    def primary_transport(self, *_a, **_k):
        primary_calls["n"] += 1
        raise RuntimeError("transient primary failure")

    monkeypatch.setattr(lc.LLMClient, "_chat_openai", primary_transport)
    fallback_calls = {"n": 0}

    def fallback_none(self, *_a, **_k):
        fallback_calls["n"] += 1
        return None

    monkeypatch.setattr(lc.LLMClient, "_try_fallback", fallback_none)
    monkeypatch.setattr(lc, "_retry_delay", lambda exc, attempt: 0.0)

    client = lc.LLMClient(provider="minimax", api_key="sk-test",
                          base_url="http://127.0.0.1:1/v1", model="MiniMax-M3")
    with pytest.raises(RuntimeError, match="transient primary failure"):
        client.chat([{"role": "user", "content": "q"}])

    assert primary_calls["n"] == lc.MAX_RETRIES
    assert fallback_calls["n"] == 1
