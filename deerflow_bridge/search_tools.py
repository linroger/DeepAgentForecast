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
"""

from __future__ import annotations

import json
import logging
import os
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
    """纯逻辑入口：选后端 → 委派 → 返回结果字符串。任何内部错误 → 带说明的空结果，绝不抛。

    调度纪律：选中的 provider 若导入不可用（无 deerflow / provider 依赖缺失），回退 DDG；
    连 DDG 也不可用则返回空结果 JSON（与 community 工具的「无结果」形状一致）。
    """
    provider = _select_search_provider()
    mod = _load_search_module(provider)
    if (mod is None or getattr(mod, "web_search_tool", None) is None) and provider != "ddg":
        # 被选后端不可用 → 回退社区 DDG（零 key 兜底路径，无需任何依赖之外的东西）
        logger.warning("search_tools: 后端 %s 不可用，回退 DDG", provider)
        mod = _load_search_module("ddg")
    tool_obj = getattr(mod, "web_search_tool", None) if mod is not None else None
    if tool_obj is None:
        return json.dumps({"error": "no web_search backend available", "query": query},
                          ensure_ascii=False)
    try:
        result = _call_delegate(tool_obj, query, max_results)
        return result if isinstance(result, str) else str(result)
    except Exception as e:  # noqa: BLE001 — 工具层最后兜底：绝不向 agent 循环抛异常
        logger.warning("search_tools: 委派 %s 失败（降级为空结果）: %s", provider, e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)


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
