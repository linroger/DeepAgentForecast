"""Offline acceptance of canonical, caller-owned four-scenario frames."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from deerflow_bridge.research_scenarios import parse_frame, probability_frame, prompt_instruction


@pytest.fixture(scope="module", autouse=True)
def source_hashes(record_testsuite_property):
    root = Path(__file__).resolve().parents[2]
    for relative in ("deerflow_bridge/research_scenarios.py", "backend/tests/test_research_scenario_frame.py"):
        record_testsuite_property("scenario_frame_sha256:" + relative,
                                 hashlib.sha256((root / relative).read_bytes()).hexdigest())


@pytest.fixture
def frame():
    return {"schema": "research-scenario-frame/v1", "horizon": "截至2030年 / through 2030",
            "scenarios": [{"id": f"SC{index}", "name": name, "probability": weight}
                          for index, name, weight in [(1, "基准", 40), (2, "上行", 30),
                                                      (3, "下行", 20), (4, "尾部风险", 10)]]}


def digest(frame):
    return hashlib.sha256(json.dumps(parse_frame(frame), sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_json_and_dict_have_canonical_order_detached_values_and_same_hash(frame):
    original = copy.deepcopy(frame)
    reordered = copy.deepcopy(frame)
    reordered["scenarios"].reverse()
    parsed = parse_frame(json.dumps(reordered, ensure_ascii=False))
    assert parsed == parse_frame(original)
    assert [row["id"] for row in parsed["scenarios"]] == ["SC1", "SC2", "SC3", "SC4"]
    assert digest(reordered) == digest(original)
    assert frame == original
    parsed["scenarios"][0]["name"] = "Changed"
    assert frame == original


@pytest.mark.parametrize("raw", [None, [], True, 1, "", "{bad-json Bearer private-token}", "[]", "null",
                                '{"schema":"wrong","schema":"research-scenario-frame/v1"}'])
def test_invalid_json_or_input_is_sanitized(raw):
    with pytest.raises(ValueError) as error:
        parse_frame(raw)
    assert str(error.value) == "Invalid research scenario frame"
    assert error.value.__suppress_context__


@pytest.mark.parametrize("field,value", [("schema", "other/v1"), ("horizon", ""), ("horizon", " \t"),
                                         ("horizon", "期" * 201), ("horizon", 2030),
                                         ("horizon", "\ud800"), ("extra", "untrusted source")])
def test_invalid_root_fields(frame, field, value):
    frame[field] = value
    with pytest.raises(ValueError, match="^Invalid research scenario frame$"):
        parse_frame(frame)


@pytest.mark.parametrize("field", ["schema", "horizon", "scenarios"])
def test_missing_root_fields(frame, field):
    del frame[field]
    with pytest.raises(ValueError):
        parse_frame(frame)


@pytest.mark.parametrize("field,value", [("id", "SC2"), ("id", "SC5"), ("id", "sc1"),
                                         ("id", 1), ("name", ""), ("name", "\t"),
                                         ("name", "x" * 201), ("name", None),
                                         ("probability", True), ("probability", "40"),
                                         ("probability", float("nan")), ("probability", float("inf")),
                                         ("probability", float("-inf")), ("probability", -1),
                                         ("probability", 101), ("probability", 10 ** 400),
                                         ("sources", ["fabricated"])])
def test_invalid_scenario_fields(frame, field, value):
    frame["scenarios"][0][field] = value
    with pytest.raises(ValueError, match="^Invalid research scenario frame$"):
        parse_frame(frame)


@pytest.mark.parametrize("field", ["id", "name", "probability"])
def test_missing_scenario_fields(frame, field):
    del frame["scenarios"][0][field]
    with pytest.raises(ValueError):
        parse_frame(frame)


@pytest.mark.parametrize("scenarios", [[], [{"id": "SC1"}], [None] * 4, {}, "SC1, SC2, SC3, SC4"])
def test_exactly_four_scenario_objects_required(frame, scenarios):
    frame["scenarios"] = scenarios
    with pytest.raises(ValueError):
        parse_frame(frame)


@pytest.mark.parametrize("weights,valid", [([0, 0, 0, 0], False), ([100, 0, 0, 0], True),
                                          ([40, 30, 20, 9], False), ([40, 30, 20, 10.0000005], True),
                                          ([40, 30, 20, 10.000002], False)])
def test_total_uses_absolute_tolerance_without_renormalizing(frame, weights, valid):
    for row, weight in zip(frame["scenarios"], weights, strict=True):
        row["probability"] = weight
    if valid:
        assert [row["probability"] for row in parse_frame(frame)["scenarios"]] == weights
    else:
        with pytest.raises(ValueError):
            parse_frame(frame)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_numbers_are_rejected(frame, token):
    raw = json.dumps(frame).replace('"probability": 40', '"probability": ' + token)
    with pytest.raises(ValueError):
        parse_frame(raw)


def test_duplicate_json_fields_are_rejected_in_nested_rows(frame):
    raw = json.dumps(frame).replace('"probability": 40', '"probability": 40, "probability": 40')
    with pytest.raises(ValueError):
        parse_frame(raw)


def test_display_names_need_not_be_unique_and_unicode_is_preserved(frame):
    frame["horizon"] = "期" * 200
    for row in frame["scenarios"]:
        row["name"] = "共同标签 🌏"
    parsed = parse_frame(frame)
    assert parsed == frame
    assert probability_frame(parsed) == [{"name": f"SC{index}", "weight": weight}
                                        for index, weight in enumerate([40, 30, 20, 10], 1)]


def test_changed_numbers_or_horizon_change_caller_hash(frame):
    original = digest(frame)
    frame["scenarios"][0]["probability"] = 41
    frame["scenarios"][1]["probability"] = 29
    assert digest(frame) != original
    weighted = digest(frame)
    frame["horizon"] = "2031"
    assert digest(frame) != weighted


def test_prompt_requires_exact_ids_weights_and_has_bounded_literal_labels(frame):
    frame["horizon"] = "期\n|" * 60
    for row in frame["scenarios"]:
        row["name"] = "\n|标题🌏" * 30
    text = prompt_instruction(frame)
    assert "Every Scenario/Probability table" in text
    assert "SC1..SC4 supersede any earlier A/B/C/D lettering" in text
    assert "Do not invent or substitute other scenario IDs" in text
    assert "report must include all four" in text
    assert "Scenario/情景" in text and "Probability/概率" in text
    assert "SC1 —" in text
    for index, weight in enumerate([40, 30, 20, 10], 1):
        assert f"| SC{index} | {weight}% |" in text
    assert "not evidence" in text
    assert "source" in text
    assert json.dumps(frame["horizon"], ensure_ascii=False) in text
    assert len(text.encode("utf-8")) <= 10_000


def test_prompt_does_not_round_or_use_exponent_notation(frame):
    for row, value in zip(frame["scenarios"], [0.0000001, 33.3333333, 33.3333333, 33.3333333], strict=True):
        row["probability"] = value
    text = prompt_instruction(frame)
    assert "| SC1 | 0.0000001% |" in text
    assert "| SC2 | 33.3333333% |" in text


def test_maximum_labels_and_smallest_floats_still_produce_bounded_prompt(frame):
    frame["horizon"] = "\x00" * 200
    for index, row in enumerate(frame["scenarios"]):
        row["name"] = "\x00" * 200
        row["probability"] = 100 if index == 0 else float.fromhex("0x0.0000000000001p-1022")
    text = prompt_instruction(frame)
    assert len(text.encode("utf-8")) < 10_000
    assert "5e-324" not in text
    assert "\\u0000" in text


def test_integer_float_and_signed_zero_have_one_caller_hash(frame):
    for row, value in zip(frame["scenarios"], [100, 0, 0, 0], strict=True):
        row["probability"] = value
    original = digest(frame)
    for row in frame["scenarios"]:
        row["probability"] = float(row["probability"]) if row["probability"] else -0.0
    assert digest(frame) == original


@pytest.mark.parametrize("helper", [probability_frame, prompt_instruction])
def test_helpers_revalidate_mutated_frame(frame, helper):
    frame["scenarios"][0]["probability"] = 99
    with pytest.raises(ValueError):
        helper(frame)
