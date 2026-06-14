"""Zep Graph 分页读取工具。

Zep 的 node/edge 列表接口使用 UUID cursor 分页，
本模块封装自动翻页逻辑（含单页重试），对调用方透明地返回完整列表。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..services.graphiti_client import InternalServerError, Zep

from .logger import get_logger
from .zep_rate_limit import is_zep_rate_limit_error, zep_retry_delay_seconds

logger = get_logger('mirofish.zep_paging')

_DEFAULT_PAGE_SIZE = 100
_MAX_NODES = 2000
# F-4-4: 边数量普遍多于节点，单独设置上限（高于 _MAX_NODES），
# 避免长/大型仿真把全部边无界载入内存并缓存。
_MAX_EDGES = 10000
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 2.0  # seconds, doubles each retry
_DEFAULT_RATE_LIMIT_BUFFER = 1.0
_DEFAULT_RATE_LIMIT_MAX_SLEEP = 90.0


def _fetch_page_with_retry(
    api_call: Callable[..., list[Any]],
    *args: Any,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
    rate_limit_buffer: float = _DEFAULT_RATE_LIMIT_BUFFER,
    rate_limit_max_sleep: float = _DEFAULT_RATE_LIMIT_MAX_SLEEP,
    page_description: str = "page",
    **kwargs: Any,
) -> list[Any]:
    """单页请求，失败时指数退避重试。仅重试网络/IO类瞬态错误。"""
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    last_exception: Exception | None = None
    delay = retry_delay

    for attempt in range(max_retries):
        try:
            return api_call(*args, **kwargs)
        except Exception as e:
            if not (
                isinstance(e, (ConnectionError, TimeoutError, OSError, InternalServerError))
                or is_zep_rate_limit_error(e)
            ):
                raise

            last_exception = e
            if attempt < max_retries - 1:
                sleep_for = (
                    zep_retry_delay_seconds(
                        e,
                        fallback=delay,
                        buffer_seconds=rate_limit_buffer,
                        max_sleep_seconds=rate_limit_max_sleep,
                    )
                    if is_zep_rate_limit_error(e)
                    else delay
                )
                logger.warning(
                    f"Zep {page_description} attempt {attempt + 1} failed: {str(e)[:100]}, retrying in {sleep_for:.1f}s..."
                )
                time.sleep(sleep_for)
                delay *= 2
            else:
                logger.error(f"Zep {page_description} failed after {max_retries} attempts: {str(e)}")

    assert last_exception is not None
    raise last_exception


def fetch_all_nodes(
    client: Zep,
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_items: int = _MAX_NODES,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
    rate_limit_buffer: float = _DEFAULT_RATE_LIMIT_BUFFER,
    rate_limit_max_sleep: float = _DEFAULT_RATE_LIMIT_MAX_SLEEP,
) -> list[Any]:
    """分页获取图谱节点，最多返回 max_items 条（默认 2000）。每页请求自带重试。"""
    all_nodes: list[Any] = []
    cursor: str | None = None
    page_num = 0

    while True:
        kwargs: dict[str, Any] = {"limit": page_size}
        if cursor is not None:
            kwargs["uuid_cursor"] = cursor

        page_num += 1
        batch = _fetch_page_with_retry(
            client.graph.node.get_by_graph_id,
            graph_id,
            max_retries=max_retries,
            retry_delay=retry_delay,
            rate_limit_buffer=rate_limit_buffer,
            rate_limit_max_sleep=rate_limit_max_sleep,
            page_description=f"fetch nodes page {page_num} (graph={graph_id})",
            **kwargs,
        )
        if not batch:
            break

        all_nodes.extend(batch)
        if len(all_nodes) >= max_items:
            all_nodes = all_nodes[:max_items]
            logger.warning(f"Node count reached limit ({max_items}), stopping pagination for graph {graph_id}")
            break
        if len(batch) < page_size:
            break

        cursor = getattr(batch[-1], "uuid_", None) or getattr(batch[-1], "uuid", None)
        if cursor is None:
            logger.warning(f"Node missing uuid field, stopping pagination at {len(all_nodes)} nodes")
            break

    return all_nodes


def fetch_all_edges(
    client: Zep,
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_items: int = _MAX_EDGES,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
    rate_limit_buffer: float = _DEFAULT_RATE_LIMIT_BUFFER,
    rate_limit_max_sleep: float = _DEFAULT_RATE_LIMIT_MAX_SLEEP,
) -> list[Any]:
    """分页获取图谱所有边，最多返回 max_items 条（默认 _MAX_EDGES）。每页请求自带重试。"""
    all_edges: list[Any] = []
    cursor: str | None = None
    page_num = 0

    while True:
        kwargs: dict[str, Any] = {"limit": page_size}
        if cursor is not None:
            kwargs["uuid_cursor"] = cursor

        page_num += 1
        batch = _fetch_page_with_retry(
            client.graph.edge.get_by_graph_id,
            graph_id,
            max_retries=max_retries,
            retry_delay=retry_delay,
            rate_limit_buffer=rate_limit_buffer,
            rate_limit_max_sleep=rate_limit_max_sleep,
            page_description=f"fetch edges page {page_num} (graph={graph_id})",
            **kwargs,
        )
        if not batch:
            break

        all_edges.extend(batch)
        # F-4-4: 与 fetch_all_nodes 一致地限制总量，防止无界载入/缓存。
        # 一旦截断，下游 _local_search/panorama_search/get_node_edges/coalition
        # 会在不完整的边集上静默运行，因此截断警告必须显眼可见于日志。
        if len(all_edges) >= max_items:
            all_edges = all_edges[:max_items]
            logger.warning(f"Edge count reached limit ({max_items}), stopping pagination for graph {graph_id}")
            break
        if len(batch) < page_size:
            break

        cursor = getattr(batch[-1], "uuid_", None) or getattr(batch[-1], "uuid", None)
        if cursor is None:
            logger.warning(f"Edge missing uuid field, stopping pagination at {len(all_edges)} edges")
            break

    return all_edges
