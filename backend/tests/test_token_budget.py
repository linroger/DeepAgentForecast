"""RQ-7 / I-6-4 — token_budget 纯函数预算工具的离线单元测试（无 LLM/网络）。

覆盖：
- estimate_tokens / truncate_to_tokens 的换算与边界（空/None/非法参数/已在预算内/超预算）；
- context_budget 的下限钳 0 与异常兜底；
- slice_budget_chars 的 floor 语义（小窗口/异常恒 >= floor）、大窗口放宽、num_items 平摊、
  cap_tokens 压顶、share 缩放；
- clamp_chars 的 [floor, ceiling] 钳制与 ceiling<floor 兜底；
- fit_to_budget 的顺序纳入、单条上限、预算耗尽即停、非法预算 degrade。
"""

import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.utils import token_budget as tb  # noqa: E402


# --- estimate_tokens ---------------------------------------------------------

def test_estimate_tokens_basic_and_ceil():
    assert tb.estimate_tokens("") == 0
    assert tb.estimate_tokens(None) == 0
    # 4 字符/token，向上取整
    assert tb.estimate_tokens("a" * 4) == 1
    assert tb.estimate_tokens("a" * 5) == 2
    assert tb.estimate_tokens("a" * 8) == 2


def test_estimate_tokens_custom_cpt_and_bad_cpt():
    assert tb.estimate_tokens("a" * 6, chars_per_token=3) == 2
    # 非法/<=0 chars_per_token → 回退默认 4
    assert tb.estimate_tokens("a" * 4, chars_per_token=0) == 1
    assert tb.estimate_tokens("a" * 4, chars_per_token="x") == 1


# --- truncate_to_tokens ------------------------------------------------------

def test_truncate_to_tokens_bounds():
    assert tb.truncate_to_tokens("abcdef", 0) == ""
    assert tb.truncate_to_tokens("abcdef", -3) == ""
    # 已在预算内 → 原样
    assert tb.truncate_to_tokens("abcd", 2) == "abcd"  # 2 token → 8 字符上限
    # 超预算 → 截到 max_tokens*cpt 字符
    assert tb.truncate_to_tokens("a" * 20, 2) == "a" * 8
    assert tb.truncate_to_tokens(None, 5) == ""


# --- context_budget ----------------------------------------------------------

def test_context_budget_floor_zero_and_used():
    assert tb.context_budget(1000, 200) == 800
    assert tb.context_budget(1000, 200, used_tokens=100) == 700
    # 预留 > 窗口 → 钳 0（不返回负数）
    assert tb.context_budget(100, 500) == 0
    # 异常输入 → 0
    assert tb.context_budget("x", None) == 0


# --- slice_budget_chars ------------------------------------------------------

def test_slice_budget_chars_small_window_holds_floor():
    # 小窗口：预算 < floor → 返回 floor（绝不收紧，degrade-safe）
    out = tb.slice_budget_chars(
        window_tokens=32000, reserved_tokens=8192, floor_chars=8000,
        share=0.5, num_items=10,
    )
    assert out == 8000


def test_slice_budget_chars_large_window_expands():
    # 大窗口（512K）：预算 >> floor → 显著放宽，携带全量前文
    out = tb.slice_budget_chars(
        window_tokens=512000, reserved_tokens=8192, floor_chars=8000,
        share=0.5, num_items=10,
    )
    assert out > 8000
    # 单章预算 ≈ (512000-8192)*0.5*4/10 ≈ 100761 字符
    assert out == int((512000 - 8192) * 0.5 * 4.0 / 10)


def test_slice_budget_chars_num_items_divides():
    single = tb.slice_budget_chars(
        window_tokens=512000, reserved_tokens=8192, floor_chars=1,
        share=1.0, num_items=1,
    )
    quad = tb.slice_budget_chars(
        window_tokens=512000, reserved_tokens=8192, floor_chars=1,
        share=1.0, num_items=4,
    )
    assert single == quad * 4


def test_slice_budget_chars_cap_tokens_limits():
    capped = tb.slice_budget_chars(
        window_tokens=1000000, reserved_tokens=8192, floor_chars=1,
        share=1.0, num_items=1, cap_tokens=10000,
    )
    # 被 cap_tokens=10000 压顶 → 10000*4 = 40000 字符
    assert capped == 40000


def test_slice_budget_chars_zero_avail_and_bad_floor():
    # 预留 >= 窗口 → avail<=0 → floor
    assert tb.slice_budget_chars(
        window_tokens=1000, reserved_tokens=2000, floor_chars=8000,
    ) == 8000
    # floor 非法 → 0（最保守兜底）
    assert tb.slice_budget_chars(
        window_tokens=512000, reserved_tokens=8192, floor_chars="oops",
    ) == 0


# --- clamp_chars -------------------------------------------------------------

def test_clamp_chars():
    assert tb.clamp_chars(5000, 1400, 3000) == 3000   # 大 → 封顶 ceiling
    assert tb.clamp_chars(800, 1400, 3000) == 1400    # 小 → 抬到 floor
    assert tb.clamp_chars(2000, 1400, 3000) == 2000   # 区间内 → 原值
    # ceiling < floor → 用 floor 兜底
    assert tb.clamp_chars(9999, 3000, 1000) == 3000
    # 异常 → floor
    assert tb.clamp_chars("x", 1400, 3000) == 1400


# --- fit_to_budget -----------------------------------------------------------

def test_fit_to_budget_sequential_and_stop():
    items = ["a" * 40, "b" * 40, "c" * 40]  # 各 10 token
    # 预算 25 token → 纳入前两条（10+10），第三条无剩余（5<需求）仍会尝试截断到 5 token=20 字符
    out = tb.fit_to_budget(items, budget_tokens=25)
    assert out[0] == "a" * 40
    assert out[1] == "b" * 40
    # 第三条被截到剩余 5 token = 20 字符
    assert out[2] == "c" * 20
    assert len(out) == 3


def test_fit_to_budget_item_max_and_empty_skip():
    items = ["a" * 100, "", None, "b" * 100]
    out = tb.fit_to_budget(items, budget_tokens=1000, item_max_tokens=5)
    # 空/None 跳过；每条截到 5 token = 20 字符
    assert out == ["a" * 20, "b" * 20]


def test_fit_to_budget_bad_budget_degrades():
    items = ["x", "y"]
    assert tb.fit_to_budget(items, budget_tokens=None) == ["x", "y"]


def test_fit_to_budget_exhausted_returns_empty():
    assert tb.fit_to_budget(["a" * 40], budget_tokens=0) == []
