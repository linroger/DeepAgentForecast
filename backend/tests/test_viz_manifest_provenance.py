"""i9 回归：viz_manifest.json 的溯源块（LOOP-017「manifests lack policy/input hashes」）。

钉住：_manifest_provenance 的确定性（同输入同哈希、异输入异哈希）、不可序列化键的诚实
跳过（unhashed_inputs 记录，绝不冒充覆盖完整）、policy 描述符 + 自哈希；_persist_manifest
的 additive 语义（无 provenance → 键不出现，i8 前字节兼容；有 → 顶层携带）。
"""

from __future__ import annotations

import hashlib
import json

from app.services.report_visualizer import ReportVisualizer


def test_provenance_hashes_are_deterministic_and_input_sensitive():
    a1 = {"forecast": {"b": 2, "a": 1}, "sim": [1, 2, 3]}
    a2 = {"sim": [1, 2, 3], "forecast": {"a": 1, "b": 2}}  # 键序不同、语义相同
    p1 = ReportVisualizer._manifest_provenance(a1)
    p2 = ReportVisualizer._manifest_provenance(a2)
    assert p1["inputs_sha256"] == p2["inputs_sha256"]  # canonical sort_keys
    assert set(p1["inputs_sha256"]) == {"forecast", "sim"}
    # 任一输入变化 → 对应哈希变化
    p3 = ReportVisualizer._manifest_provenance({"forecast": {"a": 1, "b": 3},
                                                "sim": [1, 2, 3]})
    assert p3["inputs_sha256"]["forecast"] != p1["inputs_sha256"]["forecast"]
    assert p3["inputs_sha256"]["sim"] == p1["inputs_sha256"]["sim"]
    # 哈希确实是 canonical-json 的 sha256（可独立复算）
    expected = hashlib.sha256(json.dumps(
        {"a": 1, "b": 2}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert p1["inputs_sha256"]["forecast"] == expected


def test_provenance_skips_unserializable_honestly():
    p = ReportVisualizer._manifest_provenance({
        "ok": {"x": 1},
        "bad": object(),
    })
    assert "ok" in p["inputs_sha256"]
    assert "bad" not in p["inputs_sha256"]
    assert p["unhashed_inputs"] == ["bad"]


def test_provenance_policy_block_and_hash():
    p = ReportVisualizer._manifest_provenance({})
    policy = p["policy"]
    assert policy["renderer"] == "report_visualizer"
    assert policy["theme"] == "WAVE9"
    assert policy["schema_version"] == 2
    assert "plotlyjs_inline" in policy
    expected = hashlib.sha256(json.dumps(
        policy, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert p["policy_sha256"] == expected


def test_persist_manifest_is_additive(tmp_path):
    # 无 provenance → 键不出现（i8 前字节语义）
    ReportVisualizer._persist_manifest(str(tmp_path), [{"id": "c1"}], [])
    payload = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert "provenance" not in payload
    # 有 → 顶层携带
    ReportVisualizer._persist_manifest(
        str(tmp_path), [{"id": "c1"}], [],
        provenance={"inputs_sha256": {"forecast": "ab"}, "policy": {},
                    "policy_sha256": "cd"})
    payload2 = json.loads((tmp_path / "viz_manifest.json").read_text(encoding="utf-8"))
    assert payload2["provenance"]["inputs_sha256"] == {"forecast": "ab"}
