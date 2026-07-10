"""WAVE9（research 提速）离线单测：缺口集收敛 / Retry-After 封顶 / 搜索缓存 / 增量补丁 / 退化环熔断。

与 test_bridge_search_and_cache.py / test_research_fanout.py 同构：deerflow_bridge 模块的
deerflow / langchain import 全在函数体内（或 try/except 兜底），故 backend/.venv 可直接
import 做纯逻辑测试——零网络、零 LLM、零 deerflow。claude_provider.py 模块级 import
anthropic / langchain_anthropic / langchain_core（backend venv 没有），用最小桩模块顶替后
按文件路径加载，只测纯静态的 _calc_backoff_ms 封顶逻辑。
"""

import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "deerflow_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

import deerflow_research as dr  # noqa: E402
import search_tools as st  # noqa: E402


# ================================================================ 1) 缺口集：整体替换 + 平台期检测


def test_advance_gap_set_replaces_with_fresh_by_default():
    prev = ["gap A", "gap B", "gap C"]
    fresh = ["gap B", "gap D"]
    new_gaps, plateau = dr.advance_gap_set(prev, fresh)
    assert new_gaps == ["gap B", "gap D"]  # 已闭合的 A/C 出清单（旧 merge 永不出）
    assert plateau is False


def test_advance_gap_set_empty_fresh_converges_to_empty():
    new_gaps, plateau = dr.advance_gap_set(["gap A"], [])
    assert new_gaps == []
    assert plateau is False  # 空集不是平台期，是收敛（调用方按 not _fresh 停）


def test_advance_gap_set_plateau_when_normalized_sets_equal():
    prev = ["Missing 2026 turnout data.", "  actor X stance "]
    fresh = ["missing 2026 turnout data", "Actor X   stance。"]  # 大小写/空白/尾标点差异
    new_gaps, plateau = dr.advance_gap_set(prev, fresh)
    assert plateau is True
    assert new_gaps == fresh  # 替换语义仍然生效


def test_advance_gap_set_no_plateau_on_real_progress():
    _, plateau = dr.advance_gap_set(["gap A", "gap B"], ["gap B"])
    assert plateau is False


def test_advance_gap_set_merge_fallback_keeps_old_semantics():
    prev = ["gap A"]
    fresh = ["gap B"]
    new_gaps, plateau = dr.advance_gap_set(prev, fresh, replace=False)
    assert new_gaps == ["gap A", "gap B"]  # RESEARCH_GAP_SET_REPLACE=false 的旧 merge 语义
    assert plateau is False


def test_advance_gap_set_caps_fresh_list():
    fresh = [f"gap {i}" for i in range(30)]
    new_gaps, _ = dr.advance_gap_set([], fresh, cap=20)
    assert len(new_gaps) == 20
    assert new_gaps[0] == "gap 0"


def test_adaptive_default_budget_is_three_gap_passes():
    """默认 RESEARCH_MAX_ADAPTIVE_PASSES 9 - (1 开场 + 5 相位) = 3 轮收口预算。"""
    assert dr.adaptive_passes_remaining(0, 9) == 3
    assert dr.adaptive_passes_remaining(2, 9) == 1  # 覆盖轮吃掉预算


# ================================================================ 2) Retry-After 封顶（claude_provider）


def _load_claude_provider():
    """用最小桩顶替 anthropic / langchain_anthropic / langchain_core 后按路径加载模块。"""
    if "wave9_claude_provider" in sys.modules:
        return sys.modules["wave9_claude_provider"]
    if "anthropic" not in sys.modules:
        anthropic_stub = types.ModuleType("anthropic")
        anthropic_stub.RateLimitError = type("RateLimitError", (Exception,), {})
        anthropic_stub.InternalServerError = type("InternalServerError", (Exception,), {})
        sys.modules["anthropic"] = anthropic_stub
    if "langchain_anthropic" not in sys.modules:
        lc_anthropic = types.ModuleType("langchain_anthropic")
        lc_anthropic.ChatAnthropic = type("ChatAnthropic", (), {})
        sys.modules["langchain_anthropic"] = lc_anthropic
    if "langchain_core.messages" not in sys.modules:
        lc_core = sys.modules.get("langchain_core") or types.ModuleType("langchain_core")
        lc_msgs = types.ModuleType("langchain_core.messages")
        lc_msgs.BaseMessage = type("BaseMessage", (), {})
        lc_msgs.HumanMessage = type("HumanMessage", (), {})
        lc_core.messages = lc_msgs
        sys.modules["langchain_core"] = lc_core
        sys.modules["langchain_core.messages"] = lc_msgs
    path = os.path.join(_BRIDGE_DIR, "patches", "models", "claude_provider.py")
    spec = importlib.util.spec_from_file_location("wave9_claude_provider", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wave9_claude_provider"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeRateLimitError(Exception):
    def __init__(self, headers):
        super().__init__("429")
        self.response = SimpleNamespace(headers=headers)


def test_retry_after_within_cap_is_honored(monkeypatch):
    monkeypatch.delenv("CLAUDE_RETRY_AFTER_CAP_S", raising=False)
    cp = _load_claude_provider()
    err = _FakeRateLimitError({"Retry-After": "60"})
    assert cp.ClaudeChatModel._calc_backoff_ms(1, err) == 60_000


def test_retry_after_beyond_cap_fails_fast(monkeypatch):
    """观测到的 467,421s（5.4 天）Retry-After 必须快速失败而不是长眠。"""
    monkeypatch.delenv("CLAUDE_RETRY_AFTER_CAP_S", raising=False)
    cp = _load_claude_provider()
    err = _FakeRateLimitError({"Retry-After": "467421"})
    with pytest.raises(cp.RetryAfterCapExceededError) as ei:
        cp.ClaudeChatModel._calc_backoff_ms(1, err)
    assert "weekly/plan" in str(ei.value)


def test_retry_after_cap_zero_restores_uncapped_behavior(monkeypatch):
    monkeypatch.setenv("CLAUDE_RETRY_AFTER_CAP_S", "0")
    cp = _load_claude_provider()
    err = _FakeRateLimitError({"Retry-After": "467421"})
    assert cp.ClaudeChatModel._calc_backoff_ms(1, err) == 467_421_000


def test_retry_after_custom_cap_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_RETRY_AFTER_CAP_S", "30")
    cp = _load_claude_provider()
    with pytest.raises(cp.RetryAfterCapExceededError):
        cp.ClaudeChatModel._calc_backoff_ms(1, _FakeRateLimitError({"Retry-After": "60"}))
    assert cp.ClaudeChatModel._calc_backoff_ms(1, _FakeRateLimitError({"Retry-After": "20"})) == 20_000


def test_backoff_without_header_and_with_bad_header_unchanged(monkeypatch):
    monkeypatch.delenv("CLAUDE_RETRY_AFTER_CAP_S", raising=False)
    cp = _load_claude_provider()
    # 无 header → 指数退避 + 20% buffer（attempt 1 → 2000+400）
    assert cp.ClaudeChatModel._calc_backoff_ms(1, _FakeRateLimitError({})) == 2_400
    # 非整数 header → 忽略，走退避，不抛
    assert cp.ClaudeChatModel._calc_backoff_ms(2, _FakeRateLimitError({"Retry-After": "soon"})) == 4_800


# ================================================================ 3) web_search 磁盘缓存（round-trip）


def _fake_search_backend(monkeypatch, payload: str):
    """monkeypatch 掉真实后端：_load_search_module 返回带 web_search_tool 的桩，
    _call_delegate 计数并返回 payload。返回调用计数 dict。"""
    calls = {"n": 0}
    monkeypatch.setattr(st, "_load_search_module", lambda provider: SimpleNamespace(web_search_tool=object()))
    def _delegate(tool_obj, query, max_results):
        calls["n"] += 1
        return payload
    monkeypatch.setattr(st, "_call_delegate", _delegate)
    return calls


def _cache_env(monkeypatch, tmp_path, ttl="6"):
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_DIR", str(tmp_path / "search_cache"))
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", ttl)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


def test_search_cache_round_trip_hits_on_second_call(monkeypatch, tmp_path):
    _cache_env(monkeypatch, tmp_path)
    payload = json.dumps({"results": [{"title": "t", "url": "https://example.com"}]})
    calls = _fake_search_backend(monkeypatch, payload)
    r1 = st.web_search_impl("2026 Senate midterm polls", 10)
    r2 = st.web_search_impl("2026 Senate midterm polls", 10)
    assert r1 == payload and r2 == payload
    assert calls["n"] == 1  # 第二次命中缓存，不再委派
    assert list((tmp_path / "search_cache").glob("*.json"))


def test_search_cache_normalizes_query_case_and_whitespace(monkeypatch, tmp_path):
    _cache_env(monkeypatch, tmp_path)
    payload = json.dumps({"results": [{"title": "t"}]})
    calls = _fake_search_backend(monkeypatch, payload)
    st.web_search_impl("Texas  Instruments analog revenue", 10)
    st.web_search_impl("texas instruments ANALOG revenue ", 10)
    assert calls["n"] == 1  # 近重复 query 归一化后同键


def test_search_cache_ttl_zero_disables(monkeypatch, tmp_path):
    _cache_env(monkeypatch, tmp_path, ttl="0")
    payload = json.dumps({"results": [{"title": "t"}]})
    calls = _fake_search_backend(monkeypatch, payload)
    st.web_search_impl("same query here", 10)
    st.web_search_impl("same query here", 10)
    assert calls["n"] == 2  # 关闭缓存 → 每次直连
    assert not (tmp_path / "search_cache").exists()


def test_search_cache_expired_entry_refetches(monkeypatch, tmp_path):
    _cache_env(monkeypatch, tmp_path)
    payload = json.dumps({"results": [{"title": "t"}]})
    calls = _fake_search_backend(monkeypatch, payload)
    st.web_search_impl("stale query example", 10)
    # 把缓存条目的 fetched_at 拨回 7 小时前（TTL 6h）→ 过期，重搜
    entry = next((tmp_path / "search_cache").glob("*.json"))
    obj = json.loads(entry.read_text(encoding="utf-8"))
    obj["fetched_at"] = time.time() - 7 * 3600
    entry.write_text(json.dumps(obj), encoding="utf-8")
    st.web_search_impl("stale query example", 10)
    assert calls["n"] == 2


def test_search_cache_never_caches_error_or_empty_results(monkeypatch, tmp_path):
    _cache_env(monkeypatch, tmp_path)
    calls = _fake_search_backend(monkeypatch, json.dumps({"error": "rate limited", "query": "q"}))
    st.web_search_impl("failing query text", 10)
    st.web_search_impl("failing query text", 10)
    assert calls["n"] == 2  # 失败结果不固化
    calls2 = _fake_search_backend(monkeypatch, json.dumps({"results": []}))
    st.web_search_impl("empty result query", 10)
    st.web_search_impl("empty result query", 10)
    assert calls2["n"] == 2  # 空结果不固化


def test_search_cache_key_separates_providers_and_max_results():
    k1 = st._search_cache_key("serper", "Query A", 10)
    k2 = st._search_cache_key("ddg", "Query A", 10)
    k3 = st._search_cache_key("serper", "query  a", 10)  # 归一化后同键
    k4 = st._search_cache_key("serper", "Query A", 5)
    assert k1 != k2 and k1 == k3 and k1 != k4


def test_search_cache_hermetic_under_pytest_unless_opted_in(monkeypatch):
    """测试卫生 pin：pytest 进程内未显式设 TTL → 缓存关闭（裸调 web_search_impl 的既有
    单测绝不往 <module_dir>/.cache 落盘）；显式设置 env 则照常生效。"""
    monkeypatch.delenv("RESEARCH_SEARCH_CACHE_TTL_H", raising=False)
    assert "PYTEST_CURRENT_TEST" in os.environ  # pytest 自动注入
    assert st._search_ttl_seconds() == 0.0
    monkeypatch.setenv("RESEARCH_SEARCH_CACHE_TTL_H", "6")
    assert st._search_ttl_seconds() == 6 * 3600.0


def test_is_search_cacheable_shapes():
    assert st._is_search_cacheable(json.dumps({"results": [{"t": 1}]}))
    assert not st._is_search_cacheable(json.dumps({"error": "boom"}))
    assert not st._is_search_cacheable(json.dumps({"results": []}))
    assert not st._is_search_cacheable("")
    assert not st._is_search_cacheable("short")
    assert not st._is_search_cacheable(None)
    assert st._is_search_cacheable("plain text search results that are long enough")


# ================================================================ 4) 增量报告补丁（解析 + 应用）

_REPORT = """# 卷宗

前言段落（首个 H2 之前，必须原样保留）。

## Executive Summary

原执行摘要正文。

## Actor Map

原 actor 正文，含关键事实 X。

## Scenarios

原情景正文。
"""


def test_list_report_section_titles():
    assert dr.list_report_section_titles(_REPORT) == ["Executive Summary", "Actor Map", "Scenarios"]


def test_parse_report_patch_sections_appends_and_no_changes():
    raw = (
        "<<<SECTION: Actor Map>>>\n新 actor 正文，含关键事实 X 与新佐证来源。\n<<<END>>>\n"
        "<<<APPEND>>>\n## Triangulation Verification\n核验发现……\n<<<END>>>\n"
    )
    replacements, appends, no_changes = dr.parse_report_patch(raw)
    assert replacements == [("Actor Map", "新 actor 正文，含关键事实 X 与新佐证来源。")]
    assert len(appends) == 1 and appends[0].startswith("## Triangulation Verification")
    assert no_changes is False
    r2 = dr.parse_report_patch("<<<NO_CHANGES>>>")
    assert r2 == ([], [], True)
    assert dr.parse_report_patch("no patch markers at all") is None
    assert dr.parse_report_patch("") is None


def test_apply_report_patch_replaces_matched_section_and_appends():
    patched, matched = dr.apply_report_patch(
        _REPORT,
        [("actor map", "新 actor 正文（归一化标题匹配）。")],  # 小写标题仍应匹配
        ["## Triangulation Verification\n\n核验发现。"],
    )
    assert matched == 1
    assert "前言段落" in patched                      # 前言保留
    assert "原执行摘要正文" in patched                  # 未受影响小节保留
    assert "## Actor Map" in patched                  # 原标题行保留
    assert "新 actor 正文（归一化标题匹配）。" in patched
    assert "原 actor 正文" not in patched              # 旧正文被整节替换
    assert patched.rstrip().endswith("核验发现。")      # append 落在文末


def test_apply_report_patch_unmatched_replacement_degrades_to_append():
    patched, matched = dr.apply_report_patch(_REPORT, [("Nonexistent Section", "新增内容。")], [])
    assert matched == 0
    assert "## Nonexistent Section" in patched and "新增内容。" in patched
    assert "原 actor 正文" in patched  # 原文一字不丢


# ================================================================ 5) 退化工具环：纠偏注入 + 熔断打捞


class _Event:
    def __init__(self, type_, data):
        self.type = type_
        self.data = data


def _malformed_search_event(i):
    return _Event("messages-tuple", {"type": "ai", "tool_calls": [{"name": "web_search", "args": {"query": ""}, "id": f"c{i}"}], "content": "", "id": "m1"})


def _text_event(text, msg_id="m9"):
    return _Event("messages-tuple", {"type": "ai", "content": text, "id": msg_id})


class _FakeClient:
    def __init__(self, segments):
        self._segments = list(segments)
        self.sent = []

    def stream(self, message, thread_id=None, recursion_limit=None):
        self.sent.append((message, recursion_limit))
        seg = self._segments.pop(0) if self._segments else []
        yield from seg


def _plog(tmp_path):
    return dr.ProgressLog(tmp_path / "progress.log")


def test_degenerate_loop_injects_corrective_message_then_continues(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", "8")
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", "16")
    client = _FakeClient([
        [_malformed_search_event(i) for i in range(8)],   # 段 1：连续 8 次被拒 → 触发纠偏
        [_text_event("final notes after correction")],     # 段 2：纠偏后正常产出
    ])
    plog = _plog(tmp_path)
    try:
        out = dr.run_streamed_turn(client, "original prompt", "t-degen", 100, plog, "test:degen")
    finally:
        plog.close()
    assert out == "final notes after correction"
    assert len(client.sent) == 2
    assert client.sent[0][0] == "original prompt"
    assert client.sent[1][0] == dr._DEGEN_CORRECTIVE_MESSAGE  # 注入的单行纠偏消息
    assert client.sent[1][1] == 92  # 剩余预算近似值 max(32, 100-8)


def test_degenerate_loop_breaks_and_salvages_at_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", "0")   # 关纠偏，只测熔断
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", "16")
    events = [_text_event("partial text before loop", "mA")] + [_malformed_search_event(i) for i in range(20)]
    client = _FakeClient([events])
    plog = _plog(tmp_path)
    try:
        out = dr.run_streamed_turn(client, "prompt", "t-break", 100, plog, "test:break")
    finally:
        plog.close()
    assert out == "partial text before loop"  # 熔断走 salvage，不丢已积累文本
    assert len(client.sent) == 1               # 不再开新流段
    log_text = (tmp_path / "progress.log").read_text(encoding="utf-8")
    assert "degenerate tool-loop break" in log_text


def test_degenerate_loop_counter_resets_on_valid_call(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", "8")
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", "16")
    valid = _Event("messages-tuple", {"type": "ai", "tool_calls": [{"name": "web_search", "args": {"query": "a real query"}, "id": "ok1"}], "content": "", "id": "m1"})
    events = []
    for _round in range(4):  # 7 拒 + 1 有效，循环 4 轮 → 永远到不了阈值
        events.extend(_malformed_search_event(i) for i in range(7))
        events.append(valid)
    events.append(_text_event("done", "mZ"))
    client = _FakeClient([events])
    plog = _plog(tmp_path)
    try:
        out = dr.run_streamed_turn(client, "prompt", "t-reset", 200, plog, "test:reset")
    finally:
        plog.close()
    assert out == "done"
    assert len(client.sent) == 1  # 从未触发纠偏段


def test_degen_thresholds_env_parsing(monkeypatch):
    monkeypatch.delenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", raising=False)
    monkeypatch.delenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", raising=False)
    assert dr._degen_loop_thresholds() == (8, 16)
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_CORRECT_AT", "not-a-number")
    monkeypatch.setenv("RESEARCH_DEGENERATE_TOOL_BREAK_AT", "-1")
    assert dr._degen_loop_thresholds() == (8, -1)  # 非法回退默认；<=0 表示关闭该动作


# ================================================================ 6) 杂项：注入消息的 degrade 路径


def test_inject_thread_message_degrades_to_false_without_agent():
    """backend venv 无 langgraph/langchain：注入必须安静返回 False（调用方回退 agent 回合）。"""
    class _NoAgentClient:
        def _get_runnable_config(self, thread_id):
            return {"configurable": {"thread_id": thread_id}}

        def _ensure_agent(self, cfg):
            pass

    assert dr.inject_thread_message(_NoAgentClient(), "t1", "correction") is False
