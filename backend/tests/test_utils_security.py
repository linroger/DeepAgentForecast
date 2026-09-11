"""Golden tests for the security helpers (EXECPLAN2 I-7-3, guards F-13-1/F-8-1/F-13-2)."""

import socket

import pytest

from app.utils.security import (
    quote_env_value,
    redact_secrets,
    redact_text,
    sanitize_env_value,
    validate_safe_url,
)


@pytest.fixture
def resolved_url_hosts(monkeypatch):
    """Supply deterministic resolver responses without contacting DNS servers."""
    addresses = {
        "api.openai.com": "93.184.216.34",
        "localhost": "127.0.0.1",
        "169.254.169.254": "169.254.169.254",
        "10.0.0.5": "10.0.0.5",
    }

    def fake_getaddrinfo(host, port):
        assert host in addresses, f"Unexpected URL-validation test host: {host}"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                 "", (addresses[host], port))]

    monkeypatch.setattr("app.utils.security.socket.getaddrinfo", fake_getaddrinfo)


def test_redact_secrets_masks_sensitive_keys():
    out = redact_secrets({"api_key": "sk-x", "token": "t", "provider": "minimax",
                          "nested": {"password": "p", "ok": 1}})
    assert out["api_key"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["provider"] == "minimax"
    assert out["nested"]["password"] == "***REDACTED***"
    assert out["nested"]["ok"] == 1


def test_redact_secrets_does_not_overmatch():
    out = redact_secrets({"monkey": "fine", "donkey_count": 3})
    assert out == {"monkey": "fine", "donkey_count": 3}


def test_redact_text_scrubs_inline_tokens():
    assert "***REDACTED***" in redact_text("key is sk-abcdefgh12345 here")
    assert "sk-abcdefgh12345" not in redact_text("key is sk-abcdefgh12345 here")


def test_sanitize_env_value_rejects_newlines():
    with pytest.raises(ValueError):
        sanitize_env_value("a\nINJECT=evil")
    assert sanitize_env_value("  sk-ok  ") == "sk-ok"


def test_quote_env_value():
    assert quote_env_value("MiniMax-M3") == "MiniMax-M3"
    assert quote_env_value("a b") == '"a b"'
    assert quote_env_value("a#b") == '"a#b"'


def test_validate_safe_url_allows_public_and_loopback(resolved_url_hosts):
    assert validate_safe_url("https://api.openai.com/v1")
    assert validate_safe_url("http://localhost:11434/v1")  # local LLM allowed by default


def test_validate_safe_url_blocks_metadata_and_bad_scheme(resolved_url_hosts):
    with pytest.raises(ValueError):
        validate_safe_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        validate_safe_url("ftp://example.com")


def test_validate_safe_url_block_private_mode(resolved_url_hosts):
    with pytest.raises(ValueError):
        validate_safe_url("http://localhost:11434/v1", block_private=True)
    with pytest.raises(ValueError):
        validate_safe_url("http://10.0.0.5/v1", block_private=True)
