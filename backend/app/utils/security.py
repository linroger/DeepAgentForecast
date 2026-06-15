"""Centralized security helpers: secret redaction, URL/SSRF validation, and
``.env`` value sanitization.

One place to define what "sensitive" means, so every logging / error-response /
persistence surface masks the same things and a future endpoint cannot silently
leak a key. EXECPLAN2: I-8-3, F-13-1, F-8-0, F-8-5, F-13-2, F-8-1.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

# Field names whose *values* must never be logged or echoed.
# Matches api_key/apikey/*_key/key, token/*_token, secret, password, etc.
# The bounded ``key`` alternative avoids over-matching words like "monkey".
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|password|passwd|authorization|bearer|"
    r"credential|token|(?:^|[_-])keys?(?:$|[_-]))",
    re.I,
)

# Inline secret-looking tokens to scrub from free-text (provider key prefixes).
_SECRET_TOKEN_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|sk-cp-[A-Za-z0-9_\-]{8,}|"
    r"AIza[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{20,})"
)

REDACTED = "***REDACTED***"


def redact_secrets(obj: Any) -> Any:
    """Recursively mask values whose key name looks sensitive.

    Returns a *new* structure; the input is not mutated. Non-container values
    pass through except that obvious inline tokens in strings are scrubbed.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_secrets(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_TOKEN_RE.sub(REDACTED, obj)
    return obj


def redact_text(text: str) -> str:
    """Scrub inline secret-looking tokens from a free-text string."""
    if not isinstance(text, str):
        return text
    return _SECRET_TOKEN_RE.sub(REDACTED, text)


# ---------------------------------------------------------------- env values

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_env_value(value: str) -> str:
    """Validate a value destined for ``.env``.

    Reject control chars / newlines (which would inject extra ``KEY=VALUE``
    lines or corrupt the file). Returns the trimmed value; raises ``ValueError``
    on anything unsafe so the caller surfaces a clear error.
    """
    if value is None:
        return ""
    s = str(value)
    if _CONTROL_CHARS_RE.search(s):
        raise ValueError("value contains control characters / newlines and cannot be persisted")
    return s.strip()


def quote_env_value(value: str) -> str:
    """Return a python-dotenv-safe RHS for ``KEY=<rhs>``.

    Values with spaces, ``#``, quotes or ``=`` are double-quoted with internal
    quotes/backslashes escaped; simple values are returned verbatim. Assumes the
    value has already passed :func:`sanitize_env_value` (no newlines).
    """
    s = "" if value is None else str(value)
    if s == "" or re.search(r"[\s#=\"'\\]", s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------------------------------------------------------------- URL / SSRF

# Always-blocked address families: link-local (incl. cloud metadata
# 169.254.169.254), multicast, reserved, unspecified.
def _is_always_blocked(ip) -> bool:
    return bool(
        ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_safe_url(url: str, *, block_private: bool = False) -> str:
    """Validate an operator-supplied base URL before the server fetches it.

    - require an ``http``/``https`` scheme and a host;
    - always reject link-local (cloud metadata 169.254.169.254), multicast,
      reserved and unspecified addresses;
    - when ``block_private`` is true, also reject loopback/private ranges.

    Loopback/private are allowed by default because this is a local-first tool
    that legitimately points at local LLM servers (e.g. ``http://localhost:11434``);
    set ``APP_BLOCK_PRIVATE_URLS=true`` when the API is exposed beyond loopback.

    Returns the URL on success; raises ``ValueError`` otherwise.
    """
    if not url or not isinstance(url, str):
        raise ValueError("missing URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    # Resolve every address the host maps to and reject if any is unsafe
    # (defends against DNS-rebinding to a metadata/internal address).
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise ValueError(f"cannot resolve host: {host}") from exc

    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        # Loopback is allowed by default (local-first: local LLM servers); only
        # block it under block_private. Otherwise reject metadata/link-local etc.
        if ip.is_loopback:
            if block_private:
                raise ValueError(f"refusing to connect to loopback address {addr}")
            continue
        if _is_always_blocked(ip):
            raise ValueError(f"refusing to connect to blocked address {addr}")
        if block_private and ip.is_private:
            raise ValueError(f"refusing to connect to private address {addr}")
    return url.strip()
