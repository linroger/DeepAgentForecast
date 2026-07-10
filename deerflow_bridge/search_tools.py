"""Bridge config-reflected tool: web_search 后端调度器（ITEM 6，degrade-safe）。

harness 的 `web_search` 只能在 config.yaml 里**静态**选一个 provider（serper / tavily /
ddg 各自一段 `use:`，YAML 无法按 env 条件挑选）。本模块提供**单一** `web_search` 工具，
在**调用时**按 env 里配了哪把 key 挑后端，再把调用**原样委派**给 harness 自带的对应
community 工具函数——因此行为与直接在 config 里选那个 provider **逐字节一致**：

    SERPER_API_KEY 已配 → deerflow.community.serper.tools:web_search_tool
    否则 TAVILY_API_KEY 已配 → deerflow.community.tavily.tools:web_search_tool
    都没配（零 key）→ deerflow.community.ddg_search.tools:web_search_tool（社区 DDG，无需 key）

约束（对齐 market_tools.py 的部署/自包含语义）：

* **config.yaml 里以裸模块名注册**：`use: search_tools:web_search_tool`（group: web，
  max_results: 10）。deerflow_research.py 以 ``python <deer-flow>/deerflow_research.py``
  启动，``sys.path[0]`` 即脚本目录，本模块与 config.yaml 一同同步到该目录后即可被
  harness 的 reflection 以裸模块名导入。
* **委派而非重实现**：把调用转交 harness 自己的 community 工具函数，max_results / region /
  backend 等仍由它们各自读 ``get_app_config().get_tool_config("web_search")``——由于本调度器
  以 `web_search` 之名注册，读到的正是**本** stanza（max_results: 10），故与直接配置该 provider
  完全同参。
* **deerflow.* 延迟导入**：community 工具只在调用时才 import，因此本模块在无 deerflow /
  无 langchain 的离线环境（纯逻辑单测）也能成功 import；``web_search_tool`` 变量在无
  langchain 时为 None（与 market_tools.py 同构）。
* **degrade-safe**：被选后端导入失败 → 回退 DDG；连 DDG 也不可用 → 返回带说明的空结果
  JSON，绝不向 agent 循环抛异常。零 key 配置下即 byte-equivalent 的 DDG 行为。
* **env 旋钮沿用既有 provider key 名**：SERPER_API_KEY / TAVILY_API_KEY（与 config.yaml
  注释里的 $VAR 同名，与各 community 工具自身读取的 env 同名）。
* **WAVE9 —— 搜索结果磁盘缓存**（镜像 cached_fetch.py）：RESEARCH_SEARCH_CACHE_TTL_H
  （默认 6h，0=关闭）/ RESEARCH_SEARCH_CACHE_DIR / RESEARCH_SEARCH_CACHE_MAX_MB（默认 200）。
  键 = sha256(实际后端 + 归一化 query + max_results)；只缓存成功非空结果；缓存层任何异常
  → 透明直连，绝不改变搜索结果本身。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# provider → harness community 模块路径（延迟导入，见 _load_search_module）
_PROVIDER_MODULES = {
    "serper": "deerflow.community.serper.tools",
    "tavily": "deerflow.community.tavily.tools",
    "ddg": "deerflow.community.ddg_search.tools",
}

# 默认 max_results：与 config.yaml 本 stanza 对齐（深研究单轮吃 10 条覆盖面翻倍）。
DEFAULT_MAX_RESULTS = 10

# ---------------------------------------------------------------------------
# WAVE9 —— web_search 结果磁盘缓存（镜像 cached_fetch.py 的模式，自包含不 import 桥）。
# 3 条并行研究轨 × 至多 8 个 fan-out worker 围着同一 brief 搜索，近重复 query 大量重发；
# fetch 侧早有 72h 缓存（cached_fetch.py），search 侧此前没有。这里加一层短 TTL 磁盘缓存：
#   目录  env RESEARCH_SEARCH_CACHE_DIR（默认 <module_dir>/.cache/search_cache）
#   键    sha256(provider + 归一化 query + max_results)
#   值    {provider, query, result, fetched_at(epoch), result_len}
#   TTL   env RESEARCH_SEARCH_CACHE_TTL_H（默认 6h；0=关闭缓存，透明直连）
#   上限  env RESEARCH_SEARCH_CACHE_MAX_MB（默认 200；<=0=不限；超限按 mtime LRU 淘汰）
# 绝不缓存失败/空结果（error JSON、空 results 列表、过短正文）；缓存层任何异常都被吞掉并
# 回退「直接委派并返回」，绝不改变搜索结果本身（degrade-safe）。
# ---------------------------------------------------------------------------
SEARCH_CACHE_DEFAULT_TTL_HOURS = 6.0
SEARCH_CACHE_DEFAULT_MAX_MB = 200.0
# 可缓存结果的最短长度：短于此的返回体多半是空壳/瞬时失败，不值得固化。
SEARCH_CACHE_MIN_CHARS = 20

_WS_RE = re.compile(r"\s+")


def _search_cache_root() -> str:
    """缓存目录：RESEARCH_SEARCH_CACHE_DIR 覆盖，缺省 <module_dir>/.cache/search_cache。"""
    raw = os.environ.get("RESEARCH_SEARCH_CACHE_DIR", "").strip()
    if raw:
        return os.path.expanduser(raw)
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, ".cache", "search_cache")


def _search_ttl_seconds() -> float:
    """TTL（秒）。RESEARCH_SEARCH_CACHE_TTL_H 缺省 6h；<=0 → 0（关闭缓存）。非法值回退默认。

    测试卫生：pytest 会以裸 monkeypatch 直接调 web_search_impl（不设缓存 env）——若默认
    TTL 生效会把假 payload 固化进 <module_dir>/.cache 并跨测试串味。故未显式设置 TTL 且
    处于 pytest 进程内（PYTEST_CURRENT_TEST）时视为关闭；显式设置 env 的缓存单测不受影响。
    生产/子进程运行不设 PYTEST_CURRENT_TEST，默认 6h 照常生效。
    """
    raw = os.environ.get("RESEARCH_SEARCH_CACHE_TTL_H", "").strip()
    if not raw and "PYTEST_CURRENT_TEST" in os.environ:
        return 0.0
    try:
        hours = float(raw) if raw else SEARCH_CACHE_DEFAULT_TTL_HOURS
    except (TypeError, ValueError):
        hours = SEARCH_CACHE_DEFAULT_TTL_HOURS
    return max(0.0, hours) * 3600.0


def _search_max_bytes() -> int:
    """缓存目录字节上限。RESEARCH_SEARCH_CACHE_MAX_MB 缺省 200；<=0 → 0（不限）。"""
    raw = os.environ.get("RESEARCH_SEARCH_CACHE_MAX_MB", "").strip()
    try:
        mb = float(raw) if raw else SEARCH_CACHE_DEFAULT_MAX_MB
    except (TypeError, ValueError):
        mb = SEARCH_CACHE_DEFAULT_MAX_MB
    return int(max(0.0, mb) * 1024 * 1024)


def _normalize_query(query: Any) -> str:
    """query 归一化（纯函数）：strip + 空白折叠 + 小写——让仅大小写/空白不同的近重复 query 命中同键。"""
    return _WS_RE.sub(" ", str(query or "").strip()).lower()


def _search_cache_key(provider: str, query: str, max_results: int) -> str:
    """(provider, 归一化 query, max_results) → sha256 hexdigest（稳定、文件名安全）。"""
    raw = f"{provider}\n{_normalize_query(query)}\n{int(max_results)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _search_cache_path(root: str, key: str) -> str:
    return os.path.join(root, key + ".json")


def _is_search_cacheable(result: Any) -> bool:
    """仅缓存**成功且非空**的结果：str、≥最短长度、非 error JSON、非空结果列表（纯函数）。"""
    if not isinstance(result, str):
        return False
    stripped = result.strip()
    if not stripped or len(stripped) < SEARCH_CACHE_MIN_CHARS:
        return False
    try:
        obj = json.loads(stripped)
    except ValueError:
        return True  # 非 JSON 正文（某些后端返回格式化文本）——非空即可缓存
    if isinstance(obj, dict):
        if "error" in obj:
            return False
        results = obj.get("results")
        if isinstance(results, list) and not results:
            return False
        return True
    if isinstance(obj, list):
        return bool(obj)
    return True


def _read_search_cache(path: str, ttl_seconds: float) -> Optional[str]:
    """命中且未过期 → 返回 result（并 touch mtime 供 LRU 记「近用」）；否则 None。任何异常 → None。

    过期判定基于落盘时记录的 ``fetched_at``（真实搜索时刻），不用 mtime——与 cached_fetch.py
    同一设计：命中会 touch mtime 作 LRU 近用标记，二者混用会让被反复命中的条目永不过期。
    """
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        fetched_at = float(obj.get("fetched_at") or 0.0)
        result = obj.get("result")
        if not isinstance(result, str):
            return None
        if (time.time() - fetched_at) > ttl_seconds:
            return None  # 过期 → 视作未命中（调用方将重搜覆盖）
        try:
            os.utime(path, None)
        except OSError:
            pass
        return result
    except Exception:  # noqa: BLE001 — 缓存读损坏/并发写中 → 当作未命中，degrade-safe
        return None


def _write_search_cache(path: str, provider: str, query: str, result: str) -> None:
    """原子写缓存条目（temp+replace）。best-effort：任何失败静默跳过（不影响返回结果）。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "provider": provider,
            "query": query,
            "result": result,
            "fetched_at": time.time(),
            "result_len": len(result),
        }
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.warning("search_tools: 写搜索缓存失败（跳过，不影响结果）: %s", e)


def _enforce_search_cache_cap(root: str, max_bytes: int) -> None:
    """目录总字节超上限时，按 mtime 升序（最久未用）淘汰缓存文件。best-effort，任何异常静默跳过。"""
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
        logger.warning("search_tools: 淘汰搜索缓存失败（跳过）: %s", e)


def _select_search_provider() -> str:
    """按 env 里配了哪把 key 选后端：serper > tavily > ddg（纯函数，可离线单测）。

    只读 env，不触网、不 import deerflow——供 monkeypatch 环境变量的调度单测直接调用。
    键值取 strip 后非空才算「已配」（空串=未配，与桥的 provider-key 预置空串卫生一致）。
    """
    if os.environ.get("SERPER_API_KEY", "").strip():
        return "serper"
    if os.environ.get("TAVILY_API_KEY", "").strip():
        return "tavily"
    return "ddg"


def _load_search_module(provider: str) -> Optional[Any]:
    """延迟 import 指定 provider 的 harness community 工具模块；失败返回 None（degrade-safe）。"""
    path = _PROVIDER_MODULES.get(provider)
    if not path:
        return None
    try:
        import importlib

        return importlib.import_module(path)
    except Exception as e:  # noqa: BLE001 — 无 deerflow / provider 依赖缺失 → 交由上层回退
        logger.warning("search_tools: 导入 %s 失败（%s）", path, e)
        return None


def _call_delegate(tool_obj: Any, query: str, max_results: int) -> str:
    """调用一个 harness community `web_search` 工具对象，把结果原样返回。

    community 工具是 langchain `@tool` 装饰出的 StructuredTool；其未装饰的原函数在
    ``.func`` 上。ddg/serper 的原函数签名含 max_results，tavily 只有 query（自 config 读
    max_results）——故仅在被调函数确有 max_results 形参时才传，避免 TypeError。无论是否传，
    community 工具都以 config 的 max_results 覆盖（本 stanza=10），因此结果与直接配置一致。
    """
    fn = getattr(tool_obj, "func", None) or tool_obj
    kwargs: dict[str, Any] = {"query": query}
    try:
        import inspect

        if "max_results" in inspect.signature(fn).parameters:
            kwargs["max_results"] = int(max_results)
    except (TypeError, ValueError):  # 签名不可内省/取整失败 → 只传 query（community 工具读 config）
        pass
    return fn(**kwargs)


def web_search_impl(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """纯逻辑入口：选后端 → 查缓存 → 委派 → 落缓存 → 返回结果字符串。任何内部错误 → 带说明的空结果，绝不抛。

    调度纪律：选中的 provider 若导入不可用（无 deerflow / provider 依赖缺失），回退 DDG；
    连 DDG 也不可用则返回空结果 JSON（与 community 工具的「无结果」形状一致）。
    WAVE9：委派前后各加一层短 TTL 磁盘缓存（键含**实际使用**的 provider，回退 DDG 时按 ddg 记）；
    缓存关闭（TTL<=0）或 query 归一化后为空时与旧行为逐字节一致。
    """
    provider = _select_search_provider()
    mod = _load_search_module(provider)
    if (mod is None or getattr(mod, "web_search_tool", None) is None) and provider != "ddg":
        # 被选后端不可用 → 回退社区 DDG（零 key 兜底路径，无需任何依赖之外的东西）
        logger.warning("search_tools: 后端 %s 不可用，回退 DDG", provider)
        provider = "ddg"  # WAVE9：缓存键按实际委派的后端记，避免跨后端串味
        mod = _load_search_module("ddg")
    tool_obj = getattr(mod, "web_search_tool", None) if mod is not None else None
    if tool_obj is None:
        return json.dumps({"error": "no web_search backend available", "query": query},
                          ensure_ascii=False)

    # WAVE9：缓存读——TTL<=0（关闭）或空 query 时完全绕过；缓存层异常绝不阻断搜索。
    _ttl = _search_ttl_seconds()
    _cache_path_str = ""
    if _ttl > 0 and _normalize_query(query):
        try:
            _root = _search_cache_root()
            _cache_path_str = _search_cache_path(_root, _search_cache_key(provider, query, max_results))
            hit = _read_search_cache(_cache_path_str, _ttl)
            if hit is not None:
                return hit
        except Exception as e:  # noqa: BLE001 — 缓存故障 → 当作未命中直连
            logger.warning("search_tools: 搜索缓存读取异常（跳过缓存）: %s", e)
            _cache_path_str = ""

    try:
        result = _call_delegate(tool_obj, query, max_results)
        result = result if isinstance(result, str) else str(result)
    except Exception as e:  # noqa: BLE001 — 工具层最后兜底：绝不向 agent 循环抛异常
        logger.warning("search_tools: 委派 %s 失败（降级为空结果）: %s", provider, e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    # WAVE9：缓存写——仅成功且非空的结果落盘；失败/空壳原样返回但不固化。
    if _cache_path_str:
        try:
            if _is_search_cacheable(result):
                _write_search_cache(_cache_path_str, provider, query, result)
                _enforce_search_cache_cap(_search_cache_root(), _search_max_bytes())
        except Exception as e:  # noqa: BLE001 — 落盘/淘汰失败绝不影响已取得的结果
            logger.warning("search_tools: 搜索缓存写入路径异常（跳过）: %s", e)
    return result


# ---------------------------------------------------------------------------
# harness 入口：langchain BaseTool（config.yaml `use:` 指向本变量）。
# deer-flow venv 必有 langchain_core；无 langchain 的环境（离线跑纯逻辑单测）import
# 本模块仍需成功，故 langchain 缺失时该变量为 None（与 market_tools.py 同构）。
# ---------------------------------------------------------------------------
try:
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool("web_search", parse_docstring=True)
    def web_search_tool(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
        """Search the web for information. Use this tool to find current information, news, articles, and facts from the internet.

        Args:
            query: Search keywords describing what you want to find. Be specific for better results.
            max_results: Maximum number of results to return. Default is 10.
        """
        return web_search_impl(query, max_results)

except ImportError:  # noqa: BLE001 — 离线环境无 langchain：纯逻辑仍可测
    web_search_tool = None  # type: ignore[assignment]
