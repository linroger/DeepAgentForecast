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


# ATOMIC-1: fsync opt-out for high-frequency status/progress writes.
def test_write_text_atomic_fsync_false_still_writes(tmp_path):
    """fsync=False must still atomically produce the correct content + no temp."""
    p = str(tmp_path / "status.txt")
    write_text_atomic(p, "progress=0.5", fsync=False)
    assert open(p, encoding="utf-8").read() == "progress=0.5"
    write_text_atomic(p, "progress=0.9", fsync=False)
    assert open(p, encoding="utf-8").read() == "progress=0.9"
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]
    assert leftovers == []


def test_write_json_atomic_fsync_false_roundtrip(tmp_path):
    p = str(tmp_path / "status.json")
    obj = {"stage": "run", "pct": 42, "zh": "进行中"}
    write_json_atomic(p, obj, fsync=False)
    assert json.load(open(p, encoding="utf-8")) == obj


def test_write_text_atomic_fsync_default_is_true(tmp_path):
    """Default keeps durable fsync behavior (signature back-compat)."""
    import inspect

    sig = inspect.signature(write_text_atomic)
    assert sig.parameters["fsync"].default is True
    assert inspect.signature(write_json_atomic).parameters["fsync"].default is True


def test_write_json_atomic_concurrent_same_path_no_race(tmp_path):
    """REGRESSION: many threads writing the SAME path concurrently must never raise
    (the bug: hardcoded shared `*.tmp` name → one os.replace renames it away, the other
    gets FileNotFoundError; pipeline_state.json save() vs the heartbeat thread). mkstemp
    gives each call a unique tmp, so concurrent writers are safe and the file stays valid."""
    import json as _json
    import threading
    from app.utils.atomic import write_json_atomic

    path = str(tmp_path / "pipeline_state.json")
    errors = []

    def writer(n):
        try:
            for i in range(40):
                write_json_atomic(path, {"writer": n, "i": i}, fsync=False)
        except Exception as e:  # any FileNotFoundError/race surfaces here
            errors.append(repr(e))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raced: {errors[:3]}"
    # final file is complete, valid JSON (never a half-written/truncated artifact)
    with open(path) as f:
        obj = _json.load(f)
    assert "writer" in obj and "i" in obj
