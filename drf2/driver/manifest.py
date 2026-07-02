"""Artifact manifest with content hashes + hash-verified stage reuse.

Ports pipeline_orchestrator's I-4-3 decisions (`_manifest_entry_for` / `_validate_reuse`):
every artifact is registered with sha256/bytes/stage, and a completed stage may only be
reused on resume after its registered artifacts re-verify. Conservative semantics —
never falsely kill a legitimately reusable artifact:

  * manifest missing entirely      → verify passes (degrade to existence-based reuse);
  * artifact has no manifest entry → that artifact is not checked (optional outputs);
  * entry exists but file gone     → verify FAILS (must rebuild);
  * sha256 was None at record time → compare bytes + schema probe only.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, Optional

from .legacy import ensure_backend_importable

ensure_backend_importable()
from app.utils.atomic import write_json_atomic  # noqa: E402


def sha256_file(path: str, chunk_size: int = 1 << 20) -> Optional[str]:
    """流式 sha256（1MiB 分块）；读不到返回 None（调用方按「无哈希可比」降级）。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def probe_artifact(path: str) -> bool:
    """轻量结构探针：.json 必须能解析为 dict/list；其余文件非空即可。"""
    try:
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                return isinstance(json.load(f), (dict, list))
        return os.path.getsize(path) > 0
    except (OSError, ValueError):
        return False


class ArtifactManifest:
    """manifest.json：名称 → {path, sha256, bytes, stage, recorded_at, schema_ok}。"""

    def __init__(self, path: str):
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.entries = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.entries = {}

    def save(self) -> None:
        write_json_atomic(self.path, self.entries)

    def record(self, name: str, path: str, stage: str) -> Optional[dict[str, Any]]:
        """登记一条产物；文件不存在返回 None（可选产物缺失不是错误）。"""
        if not os.path.exists(path):
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        from .state import utcnow
        entry = {
            "path": os.path.abspath(path),
            "sha256": sha256_file(path),
            "bytes": size,
            "stage": stage,
            "recorded_at": utcnow(),
            "schema_ok": probe_artifact(path),
        }
        self.entries[name] = entry
        return entry

    def record_stage(self, stage: str, specs: dict[str, str]) -> dict[str, str]:
        """批量登记 + 落盘；返回实际存在并登记成功的 {name: abspath}。"""
        recorded: dict[str, str] = {}
        for name, path in specs.items():
            entry = self.record(name, path, stage)
            if entry is not None:
                recorded[name] = entry["path"]
        self.save()
        return recorded

    def verify_stage(self, stage: str, specs: dict[str, str]) -> tuple[bool, list[str]]:
        """复用前核验（语义见模块 docstring）。返回 (ok, problems)。"""
        if not self.entries:
            return True, []  # 无清单 → 退化到存在性复用（向后兼容决策）
        # 该阶段登记过的所有条目也纳入校验（ensemble 的 seed-N/ 产物名不在默认 specs 里）。
        merged = dict(specs)
        for name in self.names_for_stage(stage):
            merged.setdefault(name, self.entries[name].get("path") or "")
        problems: list[str] = []
        for name, path in merged.items():
            entry = self.entries.get(name)
            if not isinstance(entry, dict):
                continue  # 未登记（可选产物）→ 不校验
            target = entry.get("path") or path
            if not os.path.exists(target):
                problems.append(f"{name}: file missing ({target})")
                break
            try:
                size = os.path.getsize(target)
            except OSError:
                problems.append(f"{name}: unreadable")
                break
            exp_bytes = entry.get("bytes")
            if isinstance(exp_bytes, int) and exp_bytes != size:
                problems.append(f"{name}: bytes changed {exp_bytes}→{size}")
                break
            exp_sha = entry.get("sha256")
            if isinstance(exp_sha, str) and exp_sha:
                cur = sha256_file(target)
                if cur is not None and cur != exp_sha:
                    problems.append(f"{name}: sha256 mismatch (content changed/corrupted)")
                    break
            if not probe_artifact(target):
                problems.append(f"{name}: schema probe failed")
                break
        return (not problems), problems

    def names_for_stage(self, stage: str) -> Iterable[str]:
        return [n for n, e in self.entries.items() if e.get("stage") == stage]
