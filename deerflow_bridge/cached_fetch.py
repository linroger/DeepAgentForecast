"""Bridge config-reflected tool: 跨 run 一手来源缓存（ITEM 10，透明包裹 jina web_fetch）。

深研究里同一 URL 常被跨 pass / 跨 run 反复抓取（续跑、双轨、fan-out、覆盖 top-up），Jina
reader 的重量级全文转写（监管文件 / 年报 / 长 PDF）单次可达数十秒。本模块把 harness 自带的
jina `web_fetch` 工具包成一层**磁盘缓存**：命中且未过期即秒回、且**返回类型与被包裹工具完全
一致**（str），对 agent 透明。

约束（对齐 market_tools.py / search_tools.py 的部署/自包含语义）：

* **config.yaml 里以裸模块名注册**：`use: cached_fetch:web_fetch_tool`（group: web，
  timeout: 30）。由于本包裹器以 `web_fetch` 之名注册，被委派的 jina 工具读到的正是**本**
  stanza 的 timeout（30s），与直接配置 jina 完全同参。
* **委派而非重实现**：真正抓取仍走 ``deerflow.community.jina_ai.tools:web_fetch_tool``
  （异步）；本模块只在其外侧加缓存读写。deerflow.* 延迟导入 → 无 deerflow / 无 langchain
  的离线环境也能 import；``web_fetch_tool`` 变量在无 langchain 时为 None。
* **缓存语义**：
    - 目录  env RESEARCH_SOURCE_CACHE_DIR（默认 <module_dir>/.cache/source_cache）
    - 键    sha256(url) 的 hexdigest（→ ``<hash>.json``）
    - 值    {url, content, fetched_at(epoch), content_len}
    - TTL   env RESEARCH_SOURCE_CACHE_TTL_H（默认 72h；0=关闭缓存，透明直连）
    - 上限  env RESEARCH_SOURCE_CACHE_MAX_MB（默认 500；<=0=不限；超限按 mtime LRU 淘汰）
* **绝不放入正缓存的失败/哨兵/死抓取**：jina 失败返回以 "Error:" 起头的串；正文 <200 字符
  视作死抓取。LOOP-007 账本启用时，此类 exact 结果允许一次真抓重试，随后在负缓存 TTL 内
  稳定抑制；账本未启用时仍维持原来的每次真抓行为。
* **LOOP-007 —— 跨进程预算**：正缓存命中只计 attempt、不计 network；miss 后才原子占用
  fetch global/lane 额度。预算拒绝不调用 jina delegate；账本故障 fail-open 并输出 degraded 遥测。
* **degrade-safe**：任何缓存读写/目录/淘汰异常都被吞掉并回退到「直接抓取并返回」，缓存层的
  故障绝不阻断研究主流程，也绝不改变抓取结果本身。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

try:  # copied beside this module by the bridge sync guard; absence is fail-open
    import research_budget as _research_budget
except ImportError:  # pragma: no cover - exercised only by incomplete deployments
    _research_budget = None  # type: ignore[assignment]

# 死抓取阈值：正文短于此长度（或以 "Error:" 起头）视作失败/空壳，不落盘。
DEAD_FETCH_MIN_CHARS = 200
# env 缺省
DEFAULT_TTL_HOURS = 72.0
DEFAULT_MAX_MB = 500.0
DEFAULT_LOW_QUALITY_DOMAINS = (
    "economicsummarizer.com",
    "insights.triplegains.com",
)


def _source_policy_rejection(url: str) -> Optional[str]:
    """Reject configured AI/SEO aggregators before they consume fetch/context."""
    if os.environ.get(
            "RESEARCH_ALLOW_LOW_QUALITY_SOURCES", "false").strip().lower() in {
                "1", "true", "yes", "on"}:
        return None
    try:
        from urllib.parse import urlparse

        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except Exception:
        return None
    raw = os.environ.get(
        "RESEARCH_SOURCE_DENY_DOMAINS",
        ",".join(DEFAULT_LOW_QUALITY_DOMAINS),
    )
    denied = {
        domain.strip().lower().lstrip(".")
        for domain in raw.split(",") if domain.strip()
    }
    for domain in denied:
        if host == domain or host.endswith("." + domain):
            return f"configured low-quality/AI-SEO source domain: {domain}"
    return None


def _source_policy_result(url: str, reason: str) -> str:
    return json.dumps(
        {
            "error": "source_quality_rejected",
            "url": url,
            "reason": reason,
            "message": (
                "Use the original filing, regulator, company, dataset, or a "
                "high-authority independent source instead."
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _cache_root() -> str:
    """缓存目录：RESEARCH_SOURCE_CACHE_DIR 覆盖，缺省 <module_dir>/.cache/source_cache。

    以本模块文件所在目录为基（部署目录跨 run 稳定）故缓存天然跨 run 持久。绝不在此创建目录
    （留给写入时按需 makedirs），读侧不产生副作用。
    """
    raw = os.environ.get("RESEARCH_SOURCE_CACHE_DIR", "").strip()
    if raw:
        return os.path.expanduser(raw)
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, ".cache", "source_cache")


def _ttl_seconds() -> float:
    """TTL（秒）。RESEARCH_SOURCE_CACHE_TTL_H 缺省 72h；<=0 → 0（关闭缓存）。非法值回退默认。"""
    raw = os.environ.get("RESEARCH_SOURCE_CACHE_TTL_H", "").strip()
    try:
        hours = float(raw) if raw else DEFAULT_TTL_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_TTL_HOURS
    return max(0.0, hours) * 3600.0


def _max_bytes() -> int:
    """缓存目录字节上限。RESEARCH_SOURCE_CACHE_MAX_MB 缺省 500；<=0 → 0（不限）。非法值回退默认。"""
    raw = os.environ.get("RESEARCH_SOURCE_CACHE_MAX_MB", "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_MAX_MB
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_MB
    return int(max(0.0, mb) * 1024 * 1024)


def _cache_key(url: str) -> str:
    """URL → sha256 hexdigest（稳定、文件名安全的缓存键）。"""
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()


def _cache_path(root: str, url: str) -> str:
    return os.path.join(root, _cache_key(url) + ".json")


def _is_cacheable(content: Any) -> bool:
    """仅当是**成功的、非空壳**正文才可落盘：str、非空、非 "Error:" 起头、且 ≥200 字符。"""
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped or stripped.startswith("Error:"):
        return False
    return len(content) >= DEAD_FETCH_MIN_CHARS


def _read_cache(path: str, ttl_seconds: float) -> Optional[str]:
    """命中且未过期 → 返回 content（并 touch mtime 供 LRU 记「近用」）；否则 None。任何异常 → None。

    过期判定基于落盘时记录的 ``fetched_at``（真实抓取时刻），**不**用 mtime——因为命中会 touch
    mtime 用作 LRU 近用标记，二者若混用会让被反复命中的条目永不过期。二者故意分离。
    """
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        fetched_at = float(obj.get("fetched_at") or 0.0)
        content = obj.get("content")
        if not isinstance(content, str):
            return None
        if (time.time() - fetched_at) > ttl_seconds:
            return None  # 过期 → 视作未命中（调用方将重抓覆盖）
        try:
            os.utime(path, None)  # LRU：命中即刷新 mtime 为「最近使用」（best-effort）
        except OSError:
            pass
        return content
    except Exception:  # noqa: BLE001 — 缓存读损坏/并发写中 → 当作未命中，degrade-safe
        return None


def _write_cache(path: str, url: str, content: str) -> None:
    """原子写缓存条目（temp+replace）。best-effort：任何失败静默跳过（不影响返回给 agent 的结果）。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "url": url,
            "content": content,
            "fetched_at": time.time(),
            "content_len": len(content),
        }
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.warning("cached_fetch: 写缓存失败（跳过，不影响抓取结果）: %s", e)


def _enforce_size_cap(root: str, max_bytes: int) -> None:
    """目录总字节超上限时，按 mtime 升序（最久未用）淘汰缓存文件直到回落上限内。best-effort。

    只淘汰 ``*.json`` 缓存文件；mtime 兼作 LRU 近用标记（命中会 touch）。任何异常静默跳过。
    """
    if max_bytes <= 0:
        return
    try:
        entries = []
        total = 0
        with os.scandir(root) as it:
            for e in it:
                if not e.name.endswith(".json") or not e.is_file():
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, e.path))
                total += st.st_size
        if total <= max_bytes:
            return
        entries.sort(key=lambda t: t[0])  # 最久未用在前
        for _mtime, size, p in entries:
            if total <= max_bytes:
                break
            try:
                os.remove(p)
                total -= size
            except OSError:
                continue
    except Exception as e:  # noqa: BLE001
        logger.warning("cached_fetch: 淘汰缓存失败（跳过）: %s", e)


async def cached_fetch(
    url: str,
    fetch_fn: Callable[[str], Awaitable[str]],
    revisit_reason: str = "",
) -> str:
    """缓存核心流程（可注入 ``fetch_fn`` 供单测，无网络无 deerflow）。返回类型与被包裹工具一致（str）。

    TTL<=0 → 关闭正缓存（LOOP-007 预算仍独立生效）。否则：命中未过期即返回；否则真抓，成功且可缓存
    （非失败/非哨兵/≥200 字符）才落盘 + 触发 LRU 淘汰。缓存层任何异常都不改变返回结果。
    """
    exact_key = str(url or "").strip()
    policy_rejection = _source_policy_rejection(exact_key)
    if policy_rejection:
        return _source_policy_result(exact_key, policy_rejection)
    if _research_budget is not None:
        attempt = _research_budget.admit_attempt("fetch")
        if not attempt.allowed:
            return _research_budget.denial_result("web_fetch", attempt.reason, exact_key)

    ttl = _ttl_seconds()
    root = _cache_root()
    path = _cache_path(root, url)
    if ttl > 0:
        try:
            hit = _read_cache(path, ttl)
        except Exception:  # noqa: BLE001 — 极端情况下路径计算/读取异常也不阻断抓取
            hit = None
        if hit is not None:
            if _research_budget is not None:
                if not str(revisit_reason or "").strip():
                    artifact_id = _research_budget.positive_repeat(
                        "fetch", exact_key)
                    if artifact_id:
                        return _research_budget.compact_positive_result(
                            "web_fetch", artifact_id)
                _research_budget.record_positive("fetch", exact_key)
            return hit

    if (_research_budget is not None
            and _research_budget.negative_suppressed("fetch", exact_key)):
        return _research_budget.negative_result("web_fetch", exact_key)

    claim_token = ""
    waited_for_claim = False
    if _research_budget is not None and ttl > 0:
        claim_token = _research_budget.claim_request("fetch", exact_key)
        if not claim_token:
            waited_for_claim = True
            try:
                wait_seconds = max(1, int(os.environ.get(
                    "RESEARCH_INFLIGHT_WAIT_SECONDS", "45") or "45"))
            except ValueError:
                wait_seconds = 45
            deadline = time.monotonic() + wait_seconds
            delay = 0.1
            while time.monotonic() < deadline:
                await asyncio.sleep(delay)
                delay = min(1.0, delay * 1.7)
                hit = _read_cache(path, ttl)
                if hit is not None:
                    # A singleflight follower may be an isolated subagent that
                    # cannot see the owner's model history. Share the fresh
                    # cache body in full; network dedupe must not become
                    # cross-context evidence loss.
                    _research_budget.record_positive("fetch", exact_key)
                    return hit
                claim_token = _research_budget.claim_request("fetch", exact_key)
                if claim_token:
                    break
            if not claim_token:
                return json.dumps({
                    "error": "research_inflight_timeout",
                    "tool": "web_fetch",
                    "message": "Timed out waiting for the identical in-flight fetch.",
                }, ensure_ascii=False, sort_keys=True)
    if (waited_for_claim and _research_budget is not None
            and _research_budget.negative_suppressed("fetch", exact_key)):
        _research_budget.release_request(claim_token)
        return _research_budget.negative_result("web_fetch", exact_key)

    # This reservation is deliberately after the positive-cache/singleflight
    # lookup: hits never spend real fetch allowance.
    if _research_budget is not None:
        network = _research_budget.admit_network("fetch")
        if not network.allowed:
            _research_budget.release_request(claim_token)
            return _research_budget.denial_result("web_fetch", network.reason, exact_key)

    try:
        content = await fetch_fn(url)
    except Exception:
        if _research_budget is not None:
            _research_budget.export_telemetry(force=True)
        raise
    finally:
        if "content" not in locals() and _research_budget is not None:
            _research_budget.release_request(claim_token)
    try:
        if _research_budget is not None:
            if _is_cacheable(content):
                _research_budget.clear_negative("fetch", exact_key)
                _research_budget.record_positive("fetch", exact_key)
            else:
                _research_budget.record_negative("fetch", exact_key)
        if ttl > 0 and _is_cacheable(content):
            _write_cache(path, url, content)
            _enforce_size_cap(root, _max_bytes())
        return content
    finally:
        if _research_budget is not None:
            _research_budget.release_request(claim_token)


async def _jina_delegate_fetch(url: str) -> str:
    """委派给 harness 自带的 jina `web_fetch` 异步工具；其读本 `web_fetch` stanza 的 timeout(30)。"""
    import importlib

    mod = importlib.import_module("deerflow.community.jina_ai.tools")
    tool_obj = getattr(mod, "web_fetch_tool")
    fn = getattr(tool_obj, "coroutine", None)  # async @tool 的原协程函数
    if fn is not None:
        return await fn(url)
    return await tool_obj.ainvoke({"url": url})  # 兜底：走 BaseTool 的异步调用面


# ---------------------------------------------------------------------------
# harness 入口：langchain BaseTool（config.yaml `use:` 指向本变量）。
# 无 langchain 的离线环境 import 本模块仍需成功（纯缓存逻辑可测），故缺失时该变量为 None。
# ---------------------------------------------------------------------------
try:
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool("web_fetch", parse_docstring=True)
    async def web_fetch_tool(url: str, revisit_reason: str = "") -> str:
        """Fetch the contents of a web page at a given URL.
        Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
        This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
        Do NOT add www. to URLs that do NOT have them.
        URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

        Args:
            url: The URL to fetch the contents of.
            revisit_reason: Optional specific reason the already-returned full page must be revisited. Leave empty for normal use.
        """
        return await cached_fetch(url, _jina_delegate_fetch, revisit_reason)

except ImportError:  # noqa: BLE001 — 离线环境无 langchain：纯缓存逻辑仍可测
    web_fetch_tool = None  # type: ignore[assignment]
