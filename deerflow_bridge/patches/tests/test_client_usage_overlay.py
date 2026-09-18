"""LOOP-017 F1 回归：client.py 同 id usage 快照的增量记账 overlay。

取证事实：TokenUsageAttributionMiddleware 会把子代理花费折叠进**已流出**的父
AIMessage 的累计 usage_metadata，同一 message id 的后到快照可以严格变大
（父 10 → 父+子 110）。client 的 first-seen-wins 去重把这次增长整个丢弃——
Stage-1 子代理花费从 cumulative_usage（end 事件的持久总量）里蒸发。
本套件：红基线复现旧缺陷（10→110 只记 10）、补丁后按 id 记增量且相同副本
仍零记账、缩水绝不倒扣、幂等重放、漂移 fail-closed、部署树钉子。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATCHES_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_overlay():
    spec = importlib.util.spec_from_file_location(
        "apply_subagent_overlays_under_test",
        _PATCHES_DIR / "apply_subagent_overlays.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OV = _load_overlay()

# 合成 client.py：把两个 OLD 块嵌进受控脚手架（缩进与真文件逐字节一致：
# 声明块 8 空格、闭包体 12 空格），并携带 context 子 overlay 的 marker 让其跳过。
_SYNTHETIC_CLIENT = (
    "import uuid\nfrom typing import Literal\n"
    'StreamEventType = Literal["values", "messages-tuple", "custom", "end"]\n'
    "class _Streamer:\n"
    "    _app_config = None\n"
    "    def make_accounter(self, thread_id='offline'):\n"
    + OV._CLIENT_CONTEXT_ORIGINAL
    + OV._CLIENT_USAGE_DECL_ORIGINAL
    + "        cumulative_usage = {\"input_tokens\": 0, \"output_tokens\": 0, "
      "\"total_tokens\": 0}\n"
    + "\n"
    + "        def _account_usage(msg_id, usage):\n"
    + OV._CLIENT_USAGE_BODY_ORIGINAL
    + "\n"
    + "        return _account_usage, cumulative_usage\n"
)

# Migration-anchor fixture only; the companion complete-client suite executes
# the actual vendor stream generator with real LangChain message objects.
_SYNTHETIC_CLIENT += '''
    def stream(self):
        for item in self._agent.stream(
            {}):
            if item:
                if item:
                    counted_usage = _account_usage(msg_id, msg_chunk.usage_metadata)
            for msg in messages:
                msg_id = getattr(msg, "id", None)
                if msg_id in streamed_ids:
                    if msg:
                        _account_usage(msg_id, getattr(msg, "usage_metadata", None))
                        pass
                if msg:
                    counted_usage = _account_usage(msg_id, msg.usage_metadata)
                    pass
        yield StreamEvent(type="end", data={"usage": cumulative_usage})
'''

_SYNTHETIC_TASK = "# marker: embedded clients carry the active model under\n"
_SYNTHETIC_EXECUTOR = (
    "# marker: DRF overlay: classify typed blocked outcomes\n"
    "# marker: async def _aexecute_under_lease(\n"
    "# marker: DRF overlay: provider fallback messages are failed tasks\n"
    "# marker: control_failure = _drf_subagent_control_failure(\n"
)


def _make_tree(tmp_path, client_body: str = _SYNTHETIC_CLIENT) -> Path:
    root = tmp_path / "deer-flow"
    for rel, content in (
        (OV.CLIENT_PATH, client_body),
        (OV.TASK_TOOL_PATH, _SYNTHETIC_TASK),
        (OV.EXECUTOR_PATH, _SYNTHETIC_EXECUTOR),
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _accounter_from(root: Path):
    ns: dict = {}
    exec((root / OV.CLIENT_PATH).read_text(encoding="utf-8"), ns)  # noqa: S102 — 受控合成文件
    return ns["_Streamer"]().make_accounter()


def test_unpatched_accounting_reproduces_the_undercount(tmp_path):
    """红基线：旧逻辑对同 id 10→110 只记 10（LOOP-017 取证复现）。"""
    root = _make_tree(tmp_path)
    account, cumulative = _accounter_from(root)
    account("m1", {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10})
    assert account("m1", {"input_tokens": 100, "output_tokens": 10,
                          "total_tokens": 110}) is None
    assert cumulative["total_tokens"] == 10  # 缺陷：折叠进来的 100 蒸发


def test_overlay_applies_and_is_idempotent(tmp_path):
    root = _make_tree(tmp_path)
    assert OV.apply(root) == "applied"
    src = (root / OV.CLIENT_PATH).read_text(encoding="utf-8")
    assert "counted_usage_by_id" in src
    assert "counted_usage_ids" not in src
    assert OV.apply(root) == "already_applied"


def test_patched_accounting_adds_grown_same_id_delta(tmp_path):
    root = _make_tree(tmp_path)
    OV.apply(root)
    account, cumulative = _accounter_from(root)
    first = account("m1", {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10})
    assert first == {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10}
    # 同 id 累计快照增长（父+子折叠）→ 只记正增量
    delta = account("m1", {"input_tokens": 100, "output_tokens": 10,
                           "total_tokens": 110})
    assert delta == {"input_tokens": 90, "output_tokens": 10, "total_tokens": 100}
    assert cumulative == {"input_tokens": 100, "output_tokens": 10,
                          "total_tokens": 110}
    # 相同快照重到（values-vs-messages 副本）→ 零记账
    assert account("m1", {"input_tokens": 100, "output_tokens": 10,
                          "total_tokens": 110}) is None
    assert cumulative["total_tokens"] == 110
    # 缩水绝不倒扣
    assert account("m1", {"input_tokens": 50, "output_tokens": 5,
                          "total_tokens": 55}) is None
    assert cumulative["total_tokens"] == 110
    # 无 id 快照照旧全额记账
    tail = account(None, {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6})
    assert tail == {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}
    assert cumulative["total_tokens"] == 116


def test_drifted_context_fails_closed(tmp_path):
    drifted = _SYNTHETIC_CLIENT.replace("counted_usage_ids: set", "renamed_ids: set")
    root = _make_tree(tmp_path, client_body=drifted)
    with pytest.raises(RuntimeError, match="drifted"):
        OV.apply(root)


def test_deployed_client_carries_the_overlay():
    """部署树在场时的钉子：真 client.py 必须已带增量记账（skip-if-absent 约定）。"""
    deployed = _REPO_ROOT / "deer-flow" / OV.CLIENT_PATH
    if not deployed.is_file():
        pytest.skip("deployed deer-flow tree absent")
    src = deployed.read_text(encoding="utf-8")
    assert "counted_usage_by_id" in src
    assert "counted_usage_ids" not in src


def test_existing_highwater_overlay_upgrades_to_whole_stream_contract(tmp_path):
    existing = _SYNTHETIC_CLIENT.replace(OV._CLIENT_CONTEXT_ORIGINAL, OV._CLIENT_CONTEXT_PATCHED)
    existing = existing.replace(OV._CLIENT_USAGE_DECL_ORIGINAL, OV._CLIENT_USAGE_DECL_PATCHED)
    existing = existing.replace(OV._CLIENT_USAGE_BODY_ORIGINAL, OV._CLIENT_USAGE_BODY_PATCHED)
    root = _make_tree(tmp_path, existing)
    assert OV.apply(root) == "applied"
    source = (root / OV.CLIENT_PATH).read_text()
    assert OV._CLIENT_STREAM_USAGE_MARKER in source
    assert OV._CLIENT_STREAM_BASELINE in source
    assert OV._CLIENT_VALUES_USAGE in source
    assert OV.apply(root) == "already_applied"


def test_partial_stream_overlay_marker_cannot_hide_drift(tmp_path):
    root = _make_tree(tmp_path)
    OV.apply(root)
    path = root / OV.CLIENT_PATH
    path.write_text(path.read_text().replace(OV._CLIENT_VALUES_USAGE, "", 1))
    with pytest.raises(RuntimeError, match="partial or drifted"):
        OV.apply(root)
