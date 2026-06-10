"""Helpers for recognizing and pacing Zep Cloud rate limits."""

from __future__ import annotations

import re
import time
from typing import Any


_RETRY_AFTER_RE = re.compile(r"['\"]?retry-after['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)", re.I)
_RESET_RE = re.compile(r"['\"]?x-ratelimit-reset['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)", re.I)


def is_zep_rate_limit_error(exc: Any) -> bool:
    """Return True when a Zep SDK exception represents an HTTP 429 rate limit."""
    text = str(exc).lower()
    return (
        "status_code: 429" in text
        or "status code: 429" in text
        or "rate limit exceeded" in text
        or "x-ratelimit-remaining': '0'" in text
        or '"x-ratelimit-remaining": "0"' in text
    )


def zep_retry_delay_seconds(
    exc: Any,
    *,
    fallback: float,
    buffer_seconds: float = 1.0,
    max_sleep_seconds: float | None = None,
) -> float:
    """Extract the best retry delay from Zep rate-limit metadata.

    Zep Cloud's SDK currently surfaces headers in the exception string for some
    429 responses. Prefer Retry-After, then x-ratelimit-reset, then fallback.
    """
    text = str(exc)
    delay = fallback

    retry_after = _RETRY_AFTER_RE.search(text)
    if retry_after:
        delay = float(retry_after.group(1))
    else:
        reset_at = _RESET_RE.search(text)
        if reset_at:
            delay = max(0.0, float(reset_at.group(1)) - time.time())

    delay += max(0.0, buffer_seconds)
    if max_sleep_seconds is not None:
        delay = min(delay, max_sleep_seconds)
    return max(0.0, delay)
