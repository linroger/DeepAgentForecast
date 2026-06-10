#!/usr/bin/env python3
"""Regression checks for Zep rate-limit handling and report graph caching."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import zep_tools
from app.services.zep_tools import EdgeInfo, NodeInfo, SearchResult, ZepToolsService
from app.utils.zep_rate_limit import is_zep_rate_limit_error, zep_retry_delay_seconds


RATE_LIMIT_TEXT = (
    "headers: {'date': 'Tue, 09 Jun 2026 19:40:15 GMT', "
    "'retry-after': '44', 'x-ratelimit-limit': '5', "
    "'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1781034060'}, "
    "status_code: 429, body: Rate limit exceeded for FREE plan"
)


def assert_rate_limit_parser() -> None:
    exc = RuntimeError(RATE_LIMIT_TEXT)
    assert is_zep_rate_limit_error(exc)
    assert zep_retry_delay_seconds(
        exc,
        fallback=2,
        buffer_seconds=1,
        max_sleep_seconds=90,
    ) == 45


def assert_zep_tool_retry_uses_retry_after() -> None:
    service = ZepToolsService(api_key="test-key")
    sleeps: list[float] = []
    attempts = {"count": 0}
    original_sleep = zep_tools.time.sleep

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def flaky_call() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError(RATE_LIMIT_TEXT)
        return "ok"

    try:
        zep_tools.time.sleep = fake_sleep
        assert service._call_with_retry(flaky_call, "rate-limit regression", max_retries=2) == "ok"
    finally:
        zep_tools.time.sleep = original_sleep

    assert attempts["count"] == 2
    assert sleeps == [45]


def assert_graph_snapshot_is_reused() -> None:
    service = ZepToolsService(api_key="test-key")
    calls = {"nodes": 0, "edges": 0}
    original_fetch_nodes = zep_tools.fetch_all_nodes
    original_fetch_edges = zep_tools.fetch_all_edges

    def fake_fetch_nodes(*args, **kwargs):
        calls["nodes"] += 1
        return [
            SimpleNamespace(
                uuid_="node-1",
                name="TSMC",
                labels=["Entity", "Company"],
                summary="Advanced foundry leader",
                attributes={},
            )
        ]

    def fake_fetch_edges(*args, **kwargs):
        calls["edges"] += 1
        return [
            SimpleNamespace(
                uuid_="edge-1",
                name="competes_with",
                fact="TSMC competes with Samsung in advanced process manufacturing.",
                source_node_uuid="node-1",
                target_node_uuid="node-2",
            )
        ]

    service.search_graph = lambda **kwargs: SearchResult(
        facts=["TSMC competes with Samsung in advanced process manufacturing."],
        edges=[],
        nodes=[],
        query=kwargs["query"],
        total_count=1,
    )

    try:
        zep_tools.fetch_all_nodes = fake_fetch_nodes
        zep_tools.fetch_all_edges = fake_fetch_edges
        context = service.get_simulation_context("graph-1", "semiconductor outlook")
        stats = service.get_graph_statistics("graph-1")
    finally:
        zep_tools.fetch_all_nodes = original_fetch_nodes
        zep_tools.fetch_all_edges = original_fetch_edges

    assert context["graph_statistics"]["total_nodes"] == 1
    assert context["graph_statistics"]["total_edges"] == 1
    assert stats["total_nodes"] == 1
    assert stats["total_edges"] == 1
    assert calls == {"nodes": 1, "edges": 1}
    assert isinstance(context["entities"][0]["name"], str)


if __name__ == "__main__":
    assert_rate_limit_parser()
    assert_zep_tool_retry_uses_retry_after()
    assert_graph_snapshot_is_reused()
    print("Zep rate-limit and graph-cache regression checks passed")
