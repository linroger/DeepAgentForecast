"""Serve-time inlining of the shared plotly bundle for sandboxed chart HTML.

Charts are written with ``include_plotlyjs='directory'``: each HTML is a few
KB and references a sibling ``plotly.min.js`` (one shared ~4.9MB bundle per
charts directory). Opened from disk (``file://``) the relative reference just
works. Served through the API it does not: chart HTML is deliberately delivered
inside an opaque CSP sandbox (``sandbox allow-scripts`` + ``script-src
'unsafe-inline'``) that blocks all external scripts, and relaxing that policy
would let agent-produced chart content reach app cookies/network. So the API
resolves the reference here instead — the bundle is spliced into the HTML at
serve time and the sandbox stays intact.
"""

import os
import re
import threading

_PLOTLY_SRC_RE = re.compile(
    r"<script[^>]*\bsrc=[\"']plotly\.min\.js[\"'][^>]*>\s*</script>",
    re.IGNORECASE,
)

# 极小缓存：同一部署里所有 charts 目录共享同一个 plotly 版本，键按真实路径
# +mtime+size 失效，最多保留 4 份以防多版本并存时无界增长。
_BUNDLE_CACHE_MAX = 4
_bundle_lock = threading.Lock()
_bundle_cache = {}


def inline_plotly_bundle(html_path):
    """Return the chart HTML with its sibling plotly.min.js inlined.

    Returns None when the HTML does not reference a sibling bundle, the bundle
    is missing, or the bundle is unsafe to inline — callers then serve the file
    unchanged (legacy fully-inline charts keep working, and directory-mode
    charts fall back to their built-in "bundle unavailable" notice).
    """
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    match = _PLOTLY_SRC_RE.search(html)
    if match is None:
        return None
    bundle = _read_bundle(os.path.join(os.path.dirname(html_path), "plotly.min.js"))
    if bundle is None:
        return None
    return f"{html[:match.start()]}<script>{bundle}</script>{html[match.end():]}"


def _read_bundle(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = os.path.realpath(path)
    with _bundle_lock:
        cached = _bundle_cache.get(key)
        if cached is not None and cached[0] == (stat.st_mtime_ns, stat.st_size):
            return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    # A raw '</script' in the bundle would terminate the inline tag early and
    # break out of the HTML. plotly's distributed bundle never contains one
    # (plotly itself embeds the same bytes inline in 'inline' mode); a file
    # that does is not a bundle we should splice — fail closed.
    if "</script" in content.lower():
        return None
    with _bundle_lock:
        _bundle_cache[key] = ((stat.st_mtime_ns, stat.st_size), content)
        while len(_bundle_cache) > _BUNDLE_CACHE_MAX:
            _bundle_cache.pop(next(iter(_bundle_cache)))
    return content
