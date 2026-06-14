"""Golden tests for atomic file writes (EXECPLAN2 I-7-3, guards F-0-4/F-7-6)."""

import json
import os

from app.utils.atomic import write_json_atomic, write_text_atomic


def test_write_text_atomic_creates_and_overwrites(tmp_path):
    p = str(tmp_path / "a.txt")
    write_text_atomic(p, "hello")
    assert open(p, encoding="utf-8").read() == "hello"
    write_text_atomic(p, "world")
    assert open(p, encoding="utf-8").read() == "world"


def test_write_text_atomic_creates_parent_dir(tmp_path):
    p = str(tmp_path / "sub" / "deep" / "a.txt")
    write_text_atomic(p, "x")
    assert os.path.exists(p)


def test_write_json_atomic_roundtrip(tmp_path):
    p = str(tmp_path / "d.json")
    obj = {"b": 2, "a": [1, 2, 3], "zh": "中文"}
    write_json_atomic(p, obj)
    assert json.load(open(p, encoding="utf-8")) == obj


def test_write_json_atomic_no_temp_left_behind(tmp_path):
    p = str(tmp_path / "d.json")
    write_json_atomic(p, {"k": 1})
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]
    assert leftovers == []
