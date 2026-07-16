"""结构化抽取 JSON 解析稳健性单测（Session B）。

取证（pipe_f538c1371a96，grid-storage）：17 节稠密报告的结构化抽取产出 123KB 文本，
extract_json_object 解析失败 → actors.json / quantitative.json 全部丢失 → 模拟无 cast、
无 forecast_inputs，图表无数据。根因：MiniMax 输出被上限截断 / 裹在 <think> 散文里 /
带尾逗号。硬化后的 extract_json_object 抗这三类脏输出。
"""

import os
import sys
import types
from pathlib import Path

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE = os.path.join(_REPO, "deerflow_bridge")
if _BRIDGE not in sys.path:
    sys.path.insert(0, _BRIDGE)

import deerflow_research as dr  # noqa: E402


def test_truncated_json_is_salvaged():
    """输出上限截断（数组中途断掉、括号未闭合）→ 抢救出最长可解析前缀。"""
    truncated = (
        '<think>extract now</think>\nHere:\n'
        '{"situation_brief":"grid storage 2040",'
        '"actors":[{"name":"CATL","role":"cell maker"},'
        '{"name":"Tesla","role":"integ'
    )
    obj = dr.extract_json_object(truncated)
    assert obj is not None
    assert obj.get("situation_brief") == "grid storage 2040"
    actors = obj.get("actors") or []
    # 第一个 actor 完整保住（name+role）；抢救尽量多留已完成的字段——第二个只保住
    # 已闭合的 "name":"Tesla"，截断的 "role" 被丢弃（绝不吐半截键值）。
    assert actors[0] == {"name": "CATL", "role": "cell maker"}
    assert all("role" not in a or a.get("name") == "CATL" for a in actors)
    assert actors[-1]["name"] in ("CATL", "Tesla")


def test_trailing_commas_tolerated():
    tc = ('prose {"actors":[{"name":"BYD",},],'
          '"quantitative_facts":[{"metric":"LFP cost","value":"50",},],}')
    obj = dr.extract_json_object(tc)
    assert obj is not None
    assert len(obj.get("quantitative_facts") or []) == 1


def test_richest_object_wins_among_multiple():
    """散文里的杂散 {} + 真正的富抽取对象共存 → 取命中期望键最多者。"""
    multi = ('note {"verdict":"PASS"} then the real one '
             '{"actors":[{"name":"A"}],"key_events":[{"date":"2026"}],'
             '"sources":[{"url":"x"}],"quantitative_facts":[{"metric":"m"}]}')
    obj = dr.extract_json_object(multi)
    assert obj is not None
    assert obj.get("actors") and obj.get("quantitative_facts")
    assert "verdict" not in obj


def test_single_judge_scorecard_unchanged():
    """judge 记分牌（单个小对象）行为逐字节不变——硬化不得回归 judge 路径。"""
    j = 'reasoning...\n{"verdict":"PASS","scores":{"thesis_specificity":4}}'
    obj = dr.extract_json_object(j)
    assert obj is not None and obj.get("verdict") == "PASS"


def test_fenced_json_block():
    fenced = 'text\n```json\n{"actors":[{"name":"Z"}]}\n```\nmore'
    obj = dr.extract_json_object(fenced)
    assert obj is not None and obj["actors"][0]["name"] == "Z"


def test_empty_and_garbage_return_none():
    assert dr.extract_json_object("") is None
    assert dr.extract_json_object("no json here at all") is None


def test_compact_recovery_prompt_keeps_essential_forecast_inputs():
    prompt = dr.build_extraction_recovery_prompt("English")

    assert "do not browse, call tools" in prompt
    assert '"actors"' in prompt
    assert '"relationships"' in prompt
    assert '"quantitative_facts"' in prompt
    assert "at most 80" in prompt
    assert '"sources": []' in prompt


def test_unparseable_extraction_is_preserved_by_content_hash(tmp_path):
    raw = "malformed extraction {\"actors\":["

    first = dr.preserve_unparseable_extraction(tmp_path, raw)
    second = dr.preserve_unparseable_extraction(tmp_path, raw)

    assert first == second
    assert first.startswith("structured_extraction_unparseable_")
    assert (tmp_path / first).read_text(encoding="utf-8") == raw


def test_compact_recovery_uses_bounded_tool_free_provider_path(monkeypatch):
    langchain_core = types.ModuleType("langchain_core")
    messages = types.ModuleType("langchain_core.messages")
    messages.HumanMessage = lambda content: types.SimpleNamespace(content=content)
    langchain_core.messages = messages
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
    monkeypatch.setitem(sys.modules, "langchain_core.messages", messages)
    monkeypatch.setenv("RESEARCH_EXTRACTION_RECOVERY_MAX_TOKENS", "12345")
    captured = {}
    response = types.SimpleNamespace(
        content='{"actors":[{"name":"CATL"}],"sources":[]}',
        response_metadata={"finish_reason": "stop"},
        usage_metadata={"input_tokens": 100, "output_tokens": 20},
    )

    def invoke(model_name, messages_arg, **kwargs):
        captured.update({
            "model_name": model_name,
            "messages": messages_arg,
            **kwargs,
        })
        return response, model_name

    class Log:
        def write(self, _kind, _message):
            pass

    monkeypatch.setattr(dr, "_invoke_tool_free_model", invoke)
    raw = dr.extract_structured_recovery_tool_free(
        "Grounded report", None, "minimax", Log())

    assert dr.extract_json_object(raw)["actors"][0]["name"] == "CATL"
    assert captured["model_name"] == "minimax"
    assert captured["max_output_tokens"] == 12345
    assert captured["label"] == "structured-extraction-recovery"
    assert "Grounded report" in captured["messages"][0].content
    assert raw.truncated is False
    assert raw.finish_reason == "stop"


@pytest.mark.parametrize("recovery,expected", [(False, 48000), (True, 24000)])
def test_structured_extraction_default_output_budgets(
        monkeypatch, recovery, expected):
    monkeypatch.delenv("RESEARCH_EXTRACTION_MAX_TOKENS", raising=False)
    monkeypatch.delenv("RESEARCH_EXTRACTION_RECOVERY_MAX_TOKENS", raising=False)

    assert dr._structured_extraction_max_tokens(recovery=recovery) == expected


@pytest.mark.parametrize("recovery,configured,expected", [
    (False, "999999", 48000),
    (True, "999999", 24000),
    (False, "1", 4000),
    (True, "1", 4000),
])
def test_structured_extraction_output_budgets_are_hard_clamped(
        monkeypatch, recovery, configured, expected):
    key = (
        "RESEARCH_EXTRACTION_RECOVERY_MAX_TOKENS"
        if recovery else "RESEARCH_EXTRACTION_MAX_TOKENS"
    )
    monkeypatch.setenv(key, configured)

    assert dr._structured_extraction_max_tokens(recovery=recovery) == expected


def test_known_truncation_forces_compact_recovery_even_when_prefix_parses(
        monkeypatch):
    primary = dr.StructuredExtractionText(
        '{"actors":[{"name":"Partial actor"}]}',
        finish_reason="length",
        truncated=True,
    )
    recovered = dr.StructuredExtractionText(
        '{"actors":[{"name":"Complete actor"}],"relationships":[]}',
        finish_reason="stop",
        truncated=False,
    )
    calls = []
    monkeypatch.setattr(
        dr, "extract_structured_tool_free",
        lambda *_args, **_kwargs: primary,
    )

    def recovery(*_args, **_kwargs):
        calls.append("recovery")
        return recovered

    monkeypatch.setattr(dr, "extract_structured_recovery_tool_free", recovery)

    raw, obj, failed, used = dr.extract_complete_structured_tool_free(
        "report", None, "minimax", "deep", types.SimpleNamespace(
            write=lambda *_args: None),
    )

    assert calls == ["recovery"]
    assert used is True
    assert raw is recovered
    assert obj["actors"][0]["name"] == "Complete actor"
    assert [(phase, reason) for phase, _raw, reason in failed] == [
        ("primary", "provider_truncated_output:length")
    ]


def test_empty_actor_shell_forces_compact_recovery(monkeypatch):
    monkeypatch.setattr(
        dr,
        "extract_structured_tool_free",
        lambda *_args, **_kwargs: dr.StructuredExtractionText(
            '{"actors":[]}', finish_reason="stop"),
    )
    monkeypatch.setattr(
        dr,
        "extract_structured_recovery_tool_free",
        lambda *_args, **_kwargs: dr.StructuredExtractionText(
            '{"actors":[{"name":"Recovered"}]}', finish_reason="stop"),
    )

    _raw, obj, failed, used = dr.extract_complete_structured_tool_free(
        "report", None, "minimax", "deep", types.SimpleNamespace(
            write=lambda *_args: None),
    )

    assert used is True
    assert obj["actors"][0]["name"] == "Recovered"
    assert failed[0][2] == "empty_actors"


def test_truncated_compact_recovery_never_promotes_partial_actor(monkeypatch):
    monkeypatch.setattr(
        dr,
        "extract_structured_tool_free",
        lambda *_args, **_kwargs: dr.StructuredExtractionText("not json"),
    )
    monkeypatch.setattr(
        dr,
        "extract_structured_recovery_tool_free",
        lambda *_args, **_kwargs: dr.StructuredExtractionText(
            '{"actors":[{"name":"Still partial"}]}',
            finish_reason="max_tokens",
            truncated=True,
        ),
    )

    _raw, obj, failed, used = dr.extract_complete_structured_tool_free(
        "report", None, "minimax", "deep", types.SimpleNamespace(
            write=lambda *_args: None),
    )

    assert used is True
    assert obj is None
    assert [row[2] for row in failed] == [
        "unparseable_json", "provider_truncated_output:max_tokens",
    ]


def test_rejected_extraction_candidates_are_persisted_with_integrity_metadata(
        tmp_path):
    raw = dr.StructuredExtractionText(
        '{"actors":[{"name":"Partial"}]}',
        finish_reason="length",
        truncated=True,
    )
    meta = {}
    writes = []

    records = dr.persist_structured_extraction_failures(
        tmp_path,
        [("primary", raw, "provider_truncated_output:length")],
        meta,
        lambda: writes.append(True),
    )

    assert writes == [True]
    assert records[0]["truncated"] is True
    assert records[0]["finish_reason"] == "length"
    assert meta["structured_extraction_failure"] == records[0]
    assert (tmp_path / records[0]["artifact"]).read_text(encoding="utf-8") == raw


def test_legacy_streamed_actor_fallback_is_removed_from_publication_path():
    """Opt-in flags cannot restore the unbounded partial-JSON promotion path."""
    source = Path(dr.__file__).read_text(encoding="utf-8")
    assert "RESEARCH_EXTRACT_AGENT_FALLBACK" not in source
    assert "refusing legacy " in source
    assert "streamed-agent salvage" in source
