"""Bounded archive discovery and concurrent disk-cache regression evidence."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import threading
import types

import pytest


BRIDGE = Path(__file__).resolve().parents[2] / "deerflow_bridge"


@pytest.fixture(scope="session", autouse=True)
def source_hashes(record_testsuite_property):
    for path in [Path(__file__), *(BRIDGE / name for name in (
        "research_workspace.py", "research_archive.py", "cached_fetch.py"))]:
        record_testsuite_property("archive_source_sha256:" + path.name,
                                  hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def active(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BRIDGE))
    archive = importlib.import_module("research_archive")
    store = importlib.import_module("research_workspace")
    compaction = importlib.import_module("research_compaction")
    compaction.reset_compaction_stop()
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    workspace = store.ResearchWorkspace(tmp_path / "workspace", {"run": "hardening"})
    archive.activate_workspace(workspace)
    yield archive, workspace, compaction
    archive.activate_workspace(None)
    compaction.reset_compaction_stop()


def test_search_discovers_unicode_tail_at_million_character_scale(active, monkeypatch):
    archive, workspace, _ = active
    body = "Ordinary background.\r\n" * 55000 + "证据原文：晶圆产能 revised capacity 98765.\r\n"
    ref = archive.archive_tool_result(body, "search_snippet", "source-7")
    archive.archive_tool_result("Capacity mentioned vaguely. " * 100, "tool", "source-8")
    archive.archive_tool_result("Unrelated background. " * 100, "tool", "source-9")
    before = workspace.snapshot()
    original_read = workspace._read_bytes

    def bounded_only(path):
        assert path.name == "identity.json", "query must not read whole blobs"
        return original_read(path)

    monkeypatch.setattr(workspace, "_read_bytes", bounded_only)
    monkeypatch.setattr(workspace, "snapshot", lambda: pytest.fail("full snapshot during discovery"))
    view = archive.search_evidence("revised capacity 98765", limit=2)
    hit = view["results"][0]
    assert hit["artifact_id"] == ref["id"]
    assert hit["excerpt"] == body[hit["start"]:hit["end"]]
    assert "98765" in hit["excerpt"]
    assert len(hit["excerpt"]) <= 1024
    assert hit["range_query"] == f'chars:{hit["start"]}:{hit["end"]}'
    assert "source_origin" not in hit and "path" not in hit
    assert len(view["results"]) == 2
    assert archive.search_evidence("晶圆产能")["results"][0]["artifact_id"] == ref["id"]
    monkeypatch.undo()
    # Re-enable only the engine setting undone with the read guards.
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    assert archive.source_rows() == []
    assert workspace.snapshot() == before
    recalled = archive.read_evidence(ref["id"], offset=hit["start"], limit=hit["end"] - hit["start"])
    assert hit["excerpt"] in recalled["text"]


def test_search_survives_restart_and_preserves_original_ids(active):
    archive, workspace, _ = active
    ref = archive.archive_tool_result("Mercury forecast 2027. " * 300, "task", "call")
    before = archive.search_evidence("Mercury forecast")
    reopened = type(workspace)(workspace.root, workspace.identity)
    archive.activate_workspace(reopened)
    assert archive.search_evidence("Mercury forecast") == before
    assert before["results"][0]["artifact_id"] == ref["id"]
    foreign = type(workspace)(workspace.root.parent / "foreign", {"run": "foreign"})
    archive.activate_workspace(foreign)
    assert archive.search_evidence("Mercury forecast")["results"] == []


@pytest.mark.parametrize("query,limit", [(None, 10), ("", 10), (" " * 3, 10),
    ("word" * 200, 10), ("query", 0), ("query", 21), ("query", True), ("query", 1.5)])
def test_search_rejects_invalid_input_without_terminal_stop(active, query, limit):
    archive, _, compaction = active
    with pytest.raises(ValueError):
        archive.search_evidence(query, limit)
    assert compaction.get_compaction_stop() is None


def test_search_treats_fts_syntax_as_literal_words(active):
    archive, _, _ = active
    archive.archive_tool_result("quasar retained", "task")
    assert archive.search_evidence('quasar OR " * ) --')["results"]
    assert archive.search_evidence("!!!")["results"] == []


@pytest.mark.parametrize("damage", ["blob", "metadata", "symlink", "index"])
def test_search_selected_corruption_is_terminal(active, damage, tmp_path):
    archive, workspace, compaction = active
    ref = archive.archive_tool_result("nebula immutable evidence", "task")
    path = workspace.root / ref["path"]
    if damage == "blob":
        path.write_text("nebula changed evidence!!")
    elif damage == "symlink":
        target = tmp_path / "elsewhere.txt"
        target.write_text("nebula immutable evidence")
        path.unlink()
        path.symlink_to(target)
    else:
        with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
            if damage == "metadata":
                conn.execute("UPDATE evidence_chunks SET start=start+1")
            else:
                conn.execute("DROP TABLE evidence_search")
    with pytest.raises(compaction.ResearchCompactionError):
        archive.search_evidence("nebula")
    assert compaction.get_compaction_stop() is not None


def test_search_index_migrates_old_store_and_fails_wrong_identity(active):
    archive, workspace, _ = active
    ref = archive.archive_tool_result("legacy retained discovery", "task")
    before = workspace.snapshot()
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("DROP TABLE evidence_search")
        conn.execute("DROP TABLE evidence_chunks")
    with pytest.raises(Exception, match="identity"):
        type(workspace)(workspace.root, {"run": "wrong"})
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='evidence_chunks'").fetchall()
    reopened = type(workspace)(workspace.root, workspace.identity)
    assert reopened.snapshot() == before
    archive.activate_workspace(reopened)
    assert archive.search_evidence("legacy")["results"][0]["artifact_id"] == ref["id"]


@pytest.mark.parametrize("damage", ["postings", "middle_chunk", "tail_chunk", "all_members", "extra_posting"])
def test_restart_rejects_incomplete_evidence_index(active, damage):
    archive, workspace, _ = active
    archive.archive_tool_result("nebula retained evidence. " * 150, "task")
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        if damage == "postings":
            conn.execute("INSERT INTO evidence_search(evidence_search) VALUES ('delete-all')")
        elif damage == "all_members":
            conn.execute("DELETE FROM evidence_chunks")
            conn.execute("INSERT INTO evidence_search(evidence_search) VALUES ('delete-all')")
        elif damage == "extra_posting":
            conn.execute("INSERT INTO evidence_search(rowid,terms) VALUES (999999,'nebula')")
        else:
            rows = conn.execute("SELECT id,start,end FROM evidence_chunks ORDER BY start").fetchall()
            selected = rows[1] if damage == "middle_chunk" else rows[-1]
            body = "nebula retained evidence. " * 150
            terms = " ".join(importlib.import_module("research_workspace")._search_terms(body[selected[1]:selected[2]]))
            conn.execute("INSERT INTO evidence_search(evidence_search,rowid,terms) VALUES ('delete',?,?)", (selected[0], terms))
            conn.execute("DELETE FROM evidence_chunks WHERE id=?", (selected[0],))
    with pytest.raises(importlib.import_module("research_workspace").ResearchWorkspaceError,
                       match="evidence search index"):
        type(workspace)(workspace.root, workspace.identity)


def test_restart_requires_index_for_identical_content_later_made_searchable(active):
    archive, workspace, _ = active
    body = "nebula originally partial but now retained"
    original = workspace.put_artifact(body, "unverified_partial_message")
    assert archive.search_evidence("nebula")["results"] == []
    assert workspace.put_artifact(body, "worker_output") == original
    assert archive.search_evidence("nebula")["results"][0]["artifact_id"] == original["id"]
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert conn.execute("SELECT kind FROM artifacts WHERE id=?", (original["id"],)).fetchone()[0] == "unverified_partial_message"
        conn.execute("DELETE FROM evidence_chunks")
        conn.execute("INSERT INTO evidence_search(evidence_search) VALUES ('delete-all')")
    with pytest.raises(importlib.import_module("research_workspace").ResearchWorkspaceError,
                       match="evidence search index"):
        type(workspace)(workspace.root, workspace.identity)


@pytest.mark.parametrize("later_searchable", [False, True])
def test_search_eligibility_migrates_old_registry_without_changing_original_kind(active, later_searchable):
    archive, workspace, _ = active
    body = "nebula historically partial"
    ref = workspace.put_artifact(body, "unverified_partial_message")
    if later_searchable:
        assert workspace.put_artifact(body, "worker_output") == ref
    archive.archive_tool_result("separate ordinary evidence", "task")
    before = workspace.snapshot()
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM artifacts")]
        conn.execute("ALTER TABLE artifacts DROP COLUMN searchable")
        for row in rows:
            row.pop("searchable")
            row.pop("record_sha256")
            conn.execute("UPDATE artifacts SET record_sha256=? WHERE id=?",
                         (workspace._digest("artifact", row), row["id"]))
    reopened = type(workspace)(workspace.root, workspace.identity)
    assert reopened.snapshot() == before
    archive.activate_workspace(reopened)
    hits = archive.search_evidence("nebula")["results"]
    assert bool(hits) is later_searchable
    if hits:
        assert hits[0]["artifact_id"] == ref["id"]
        assert hits[0]["kind"] == "unverified_partial_message"
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert conn.execute("SELECT kind,searchable FROM artifacts WHERE id=?", (ref["id"],)).fetchone() == (
            "unverified_partial_message", int(later_searchable),
        )


def test_search_tool_schema_and_json_output(active, monkeypatch):
    archive, workspace, _ = active
    module = types.ModuleType("langchain_core.tools")
    module.tool = lambda *args, **kwargs: lambda function: function
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = importlib.util.spec_from_file_location("search_tool_fixture", BRIDGE / "research_archive.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    loaded.activate_workspace(workspace)
    ref = archive.archive_tool_result("orbital telescope retained", "task")
    assert list(inspect.signature(loaded.search_evidence_tool).parameters) == ["query", "limit"]
    assert json.loads(loaded.search_evidence_tool("telescope"))["results"][0]["artifact_id"] == ref["id"]


def test_search_skips_stream_fragments_but_keeps_full_result(active):
    archive, workspace, _ = active
    for kind in ("task_receipt", "assistant_delta", "native_pass_message", "unverified_partial_message"):
        workspace.put_artifact("nebula partial " + kind, kind)
    ref = workspace.put_artifact("nebula completed notes", "worker_output")
    assert [row["artifact_id"] for row in archive.search_evidence("nebula")["results"]] == [ref["id"]]
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM evidence_chunks").fetchone()[0] == 1


def test_migration_does_not_salvage_corrupt_base(active):
    archive, workspace, _ = active
    ref = archive.archive_tool_result("legacy retained discovery", "task")
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        conn.execute("DROP TABLE evidence_search")
        conn.execute("DROP TABLE evidence_chunks")
    (workspace.root / ref["path"]).write_text("changed")
    with pytest.raises(Exception, match="hash"):
        type(workspace)(workspace.root, workspace.identity)
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='evidence_chunks'").fetchall()


def test_failed_index_write_rolls_back_artifact_receipt_and_recovers_orphan(active, monkeypatch):
    archive, workspace, _ = active
    original = workspace._index_artifact

    def fail(*args):
        original(*args)
        raise RuntimeError("interrupted before index commit")

    monkeypatch.setattr(workspace, "_index_artifact", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        workspace.put_artifact("recoverable orphan", "evidence")
    assert workspace.snapshot()["artifacts"] == []
    assert archive.search_evidence("recoverable")["results"] == []
    monkeypatch.setattr(workspace, "_index_artifact", original)
    ref = workspace.put_artifact("recoverable orphan", "evidence")
    assert archive.search_evidence("recoverable")["results"][0]["artifact_id"] == ref["id"]


def test_concurrent_identical_archives_have_one_index_and_exact_refs(active):
    archive, workspace, _ = active
    with ThreadPoolExecutor(max_workers=5) as pool:
        refs = list(pool.map(lambda _: workspace.put_artifact("orbital result. " * 500, "evidence"), range(10)))
    assert all(ref == refs[0] for ref in refs)
    assert len(archive.search_evidence("orbital")["results"]) == 1
    with sqlite3.connect(workspace.root / "workspace.sqlite3") as conn:
        rows = conn.execute("SELECT artifact_id,start FROM evidence_chunks").fetchall()
        assert len(rows) == len(set(rows))


def test_archive_recall_preview_and_discovery_use_saved_policy(active, monkeypatch):
    archive, workspace, _ = active
    policy = archive.ContextPolicy(retrieval_tokens=16000)
    saved = type(workspace)(workspace.root.parent / "saved", {
        "run": "pinned", "context_policy": policy.to_dict(),
    })
    archive.activate_workspace(saved)
    monkeypatch.setenv("RESEARCH_AGENTIC_RETRIEVAL_TOKENS", "broken-ambient-value")
    body = "# evidence\r\n" + "orbital measurements 2027\r\n" * 1000
    ref = archive.archive_tool_result(body, "task")
    assert archive.read_evidence(ref["id"])["budget_tokens"] == 16000
    assert "Full tool output archived" in archive.evidence_preview(ref, max_chars=5000)
    for index in range(20):
        archive.archive_tool_result("orbital " + str(index) + " retained value " * 100, "task", str(index))
    result = archive.search_evidence("orbital", 20)
    assert result["truncated"] is True
    assert 0 < len(result["results"]) < 20
    assert len(archive._json(result).encode("utf-8")) <= 16000


def test_concurrent_cache_writes_publish_distinct_complete_temporary_files(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BRIDGE))
    fetch = importlib.import_module("cached_fetch")
    path = str(tmp_path / "cache.json")
    barrier = threading.Barrier(2)
    real_replace = fetch.os.replace
    published = []
    failures = []

    def replace(src, dst):
        published.append(src)
        barrier.wait(timeout=5)
        try:
            return real_replace(src, dst)
        except OSError as error:
            failures.append(error)
            raise

    monkeypatch.setattr(fetch.os, "replace", replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda body: fetch._write_cache(path, "https://example.test/source", body),
                      ["A" * 100000, "B" * 200000]))
    assert len(set(published)) == 2
    assert not failures
    assert json.loads(Path(path).read_text())["content"] in {"A" * 100000, "B" * 200000}
    assert list(tmp_path.iterdir()) == [Path(path)]
