"""Offline unit tests for deep-research fan-out (EXECPLAN2 I-0-4).

deerflow_research.py imports the deerflow harness lazily (inside functions), so its
pure helpers — KIQ/actor seed extraction + prompt builders — import and test fine
under backend/.venv without the deerflow venv. The concurrent worker execution
itself needs a live DeerFlowClient and is exercised only end-to-end.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deerflow_bridge"))

import deerflow_research as dr  # noqa: E402


OPENING = """# 研究开场：态势扫描

## 关键参与者 (Key Actors)
- **OpenAI**：前沿模型领跑者
- 美国商务部 — 出口管制主体
- **英伟达 (NVIDIA)**: GPU 供给瓶颈

## 背景叙述
一些不应被当作种子的散文段落。

## 关键问题 (KIQ)
1. HBM 产能在 2030 年的瓶颈在哪里？
2. 出口管制如何重塑供给？

## 杂项
- 这是普通小节里的条目
"""


def test_extract_kiqs_prefers_priority_sections_and_caps_width():
    seeds = dr.extract_kiqs_from_opening(OPENING, width=4)
    assert len(seeds) == 4
    # all four should come from the priority (actors / KIQ) sections, not 背景/杂项
    assert "OpenAI" in seeds
    assert any("英伟达" in s or "NVIDIA" in s for s in seeds)
    assert any("HBM" in s for s in seeds)
    assert "这是普通小节里的条目" not in seeds  # generic section deprioritized (capped out)


def test_extract_kiqs_clean_seed_strips_markdown_and_separator():
    # '**英伟达 (NVIDIA)**: GPU...' → bold stripped, trailing-after-':' trimmed
    seeds = dr.extract_kiqs_from_opening(OPENING, width=10)
    assert any("英伟达" in s for s in seeds)        # NVIDIA actor captured
    assert all("**" not in s for s in seeds)        # bold markers stripped
    assert all("GPU 供给瓶颈" not in s for s in seeds)  # text after ':' trimmed
    assert "美国商务部" in seeds                     # ' — 出口管制主体' trimmed


def test_extract_kiqs_dedup_case_insensitive():
    txt = "## actors\n- OpenAI\n- openai\n- **OpenAI**\n"
    seeds = dr.extract_kiqs_from_opening(txt, width=5)
    assert seeds == ["OpenAI"]   # all three collapse to one


def test_extract_kiqs_fallback_to_plain_bullets_when_no_priority():
    txt = "# Summary\n- First topic\n- Second topic\n\nsome prose\n"
    seeds = dr.extract_kiqs_from_opening(txt, width=2)
    assert seeds == ["First topic", "Second topic"]


def test_extract_kiqs_empty_and_short_filtered():
    assert dr.extract_kiqs_from_opening("", 4) == []
    assert dr.extract_kiqs_from_opening("no markdown structure at all", 4) == []
    assert dr.extract_kiqs_from_opening("## actors\n- a\n- ok name\n", 4) == ["ok name"]  # 'a' too short
    assert dr.extract_kiqs_from_opening(OPENING, 0) == []  # width < 1


def test_scoped_worker_prompt_contains_focus_and_question():
    p = dr.build_scoped_worker_prompt("Will X happen by 2030?", "NVIDIA supply", "Chinese")
    assert "Will X happen by 2030?" in p
    assert "NVIDIA supply" in p
    assert "Chinese" in p
    assert "do not write the full report" in p.lower()


def test_fanout_absorption_prompt_truncates_and_embeds_notes():
    big = "N" * 30000
    p = dr.build_fanout_absorption_prompt("Q?", big, None)
    assert "truncated" in p
    assert "Q?" in p
    assert "PARALLEL SUB-INVESTIGATION NOTES" in p
