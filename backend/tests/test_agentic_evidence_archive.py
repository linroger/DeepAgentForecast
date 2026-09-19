"""Offline source retention, scoped recall, and native offloading contracts."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "deerflow_bridge"
BODY = "# Original release\r\n\r\n" + "Complete source paragraph.\r\n\r\n" * 650 + "TAIL_NEEDLE: final revised figure is 98765.\r\n"
URL = "https://example.test/release"


@pytest.fixture
def active(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(BRIDGE))
    archive = importlib.import_module("research_archive")
    fetch = importlib.import_module("cached_fetch")
    store = importlib.import_module("research_workspace")
    compaction = importlib.import_module("research_compaction")
    compaction.reset_compaction_stop()
    monkeypatch.setenv("RESEARCH_ENGINE", "agentic")
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("RESEARCH_SOURCE_CACHE_TTL_H", "72")
    monkeypatch.setenv("RESEARCH_BUDGET_DB", str(tmp_path / "budget.sqlite3"))
    monkeypatch.setenv("RESEARCH_BUDGET_TELEMETRY_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("RESEARCH_BUDGET_LANE_ID", "archive-fixture")
    monkeypatch.setenv("RESEARCH_COMPACT_REPEAT_RESULTS", "true")
    workspace = store.ResearchWorkspace(tmp_path / "workspace", {"run": "fixture", "engine": "agentic"})
    archive.activate_workspace(workspace)
    yield archive, fetch, workspace, compaction
    archive.activate_workspace(None)
    compaction.reset_compaction_stop()


def test_archived_tail_recalled_with_exact_full_body_and_scoped_id(active):
    archive, _, workspace, _ = active
    ref = archive.archive_tool_result(BODY, "web_fetch", "call-1")
    assert workspace.read_artifact(ref) == BODY
    view = archive.read_evidence(ref["id"], query="TAIL_NEEDLE 98765")
    assert "TAIL_NEEDLE" in view["text"]
    assert view["estimated_tokens"] <= view["budget_tokens"]
    assert archive.source_rows() == []  # Generic tool results never become fetched sources.
    for bad_id in ("../../secret", "/etc/passwd", "blobs/" + ref["id"] + ".txt", "f" * 64):
        with pytest.raises((ValueError, KeyError, RuntimeError)):
            archive.read_evidence(bad_id)


def test_two_agents_and_restart_get_full_body_with_one_delegate_call(active):
    archive, fetch, workspace, _ = active
    calls = []
    async def producer(url):
        calls.append(url)
        await asyncio.sleep(0.02)
        fetch._FETCH_PROVIDER.set("fixture_provider")
        return BODY
    async def two_agents():
        return await asyncio.gather(fetch.cached_fetch(URL, producer), fetch.cached_fetch(URL, producer))
    assert asyncio.run(two_agents()) == [BODY, BODY]
    assert calls == [URL]
    rows = archive.source_rows()
    assert len(rows) == 1
    assert rows[0]["source_origin"] == "fetched"
    assert rows[0]["provider"] == "fixture_provider"
    assert rows[0]["content_sha256"] == hashlib.sha256(BODY.encode()).hexdigest()
    assert rows[0]["receipt_id"]
    assert workspace.snapshot()["tasks"] == []
    events = workspace.events("source:" + hashlib.sha256(URL.encode()).hexdigest())
    assert len(events) == 1 and events[0]["kind"] == "native_source"
    Path(fetch._cache_path(fetch._cache_root(), URL)).unlink()
    reopened = type(workspace)(workspace.root, workspace.identity)
    archive.activate_workspace(reopened)
    assert archive.source_rows() == rows
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == BODY
    assert calls == [URL]
    assert "TAIL_NEEDLE" in archive.read_evidence(rows[0]["ref"]["id"], query="TAIL_NEEDLE")["text"]


def test_attach_requires_matching_url_hash_and_preserves_snippet_origin(active):
    archive, fetch, _, _ = active
    async def producer(_):
        return BODY
    asyncio.run(fetch.cached_fetch(URL, producer))
    row = archive.source_rows()[0]
    rows = [dict(row, custom={"legitimate": True}), dict(row, content_sha256="0" * 64),
            dict(row, source_origin="search_snippet", excerpt="search lead"),
            {"url": URL, "source_origin": "fetched"}, dict(row, url=URL + "/wrong"),
            dict(row, source_origin="cited", excerpt="citation only")]
    before = copy.deepcopy(rows)
    attached = archive.attach_source_content(rows)
    assert attached[0]["content"] == BODY
    assert attached[0]["custom"] == {"legitimate": True}
    assert all("content" not in entry for entry in attached[1:])
    assert attached[2]["source_origin"] == "search_snippet"
    assert rows == before


@pytest.mark.parametrize("body", ["Error: unavailable " + "x" * 500,
    "Access denied. " + "x" * 500,
    json.dumps({"status": "already_available", "artifact_id": "fetch:old", "message": "x" * 500}),
    json.dumps({"error": "research_budget_exhausted", "message": "x" * 500}),
    json.dumps({"source_origin": "search_snippet", "snippet": "x" * 500}),
    json.dumps({"status": "failed", "message": "x" * 500}), "small page"])
def test_errors_controls_and_snippets_never_record_source_success(active, body):
    archive, fetch, workspace, _ = active
    async def producer(_):
        return body
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == body
    assert archive.source_rows() == []
    assert workspace.snapshot()["events"] == []
    assert not Path(fetch._cache_path(fetch._cache_root(), URL)).exists()
    assert fetch._research_budget.list_fetched_sources() == []


def test_successful_json_body_is_retained(active):
    archive, fetch, _, _ = active
    body = json.dumps({"status": "active", "observations": list(range(4000))})
    async def producer(_):
        return body
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == body
    assert archive.attach_source_content(archive.source_rows())[0]["content"] == body


def test_archive_failure_prevents_positive_record_or_body_return(active, monkeypatch):
    archive, fetch, workspace, compaction = active
    def fail(*args, **kwargs):
        raise OSError("private disk detail")
    monkeypatch.setattr(workspace, "put_artifact", fail)
    async def producer(_):
        return BODY
    with pytest.raises(compaction.ResearchCompactionError) as failure:
        asyncio.run(fetch.cached_fetch(URL, producer))
    assert "private disk detail" not in str(failure.value)
    assert compaction.get_compaction_stop() is not None
    assert archive.source_rows() == []
    assert fetch._research_budget.list_fetched_sources() == []
    assert not Path(fetch._cache_path(fetch._cache_root(), URL)).exists()


def test_source_event_failure_stops_even_if_blob_write_succeeded(active, monkeypatch):
    _, fetch, workspace, compaction = active
    def fail(*args, **kwargs):
        raise OSError("event commit failure")
    monkeypatch.setattr(workspace, "append_event", fail)
    async def producer(_):
        return BODY
    with pytest.raises(compaction.ResearchCompactionError):
        asyncio.run(fetch.cached_fetch(URL, producer))


def test_generic_tool_metadata_retained_without_source_promotion(active):
    archive, _, workspace, _ = active
    content = [{"type": "text", "text": BODY, "citation_metadata": {"url": URL}},
               {"type": "text", "text": "derived answer", "source_origin": "fetched"}]
    ref = archive.archive_tool_result(content, "task", "child-call")
    assert json.loads(workspace.read_artifact(ref)) == content
    assert archive.source_rows() == []


def test_foreign_or_corrupt_archived_body_never_recalled(active, tmp_path):
    archive, _, workspace, _ = active
    foreign = type(workspace)(tmp_path / "foreign", {"run": "other"})
    ref = foreign.put_artifact(BODY, "tool_result")
    with pytest.raises((ValueError, KeyError, RuntimeError)):
        archive.read_evidence(ref["id"])
    ref = archive.archive_tool_result(BODY, "task")
    (workspace.root / ref["path"]).write_text("tampered")
    with pytest.raises((ValueError, KeyError, RuntimeError)):
        archive.read_evidence(ref["id"])


@pytest.mark.parametrize("engine", ["legacy", "linear-v2", ""])
def test_non_agentic_engine_preserves_legacy_compact_repeat(active, monkeypatch, engine):
    archive, fetch, _, _ = active
    monkeypatch.setenv("RESEARCH_ENGINE", engine)
    assert archive.current_workspace() is None
    async def producer(_):
        return BODY
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == BODY
    second = json.loads(asyncio.run(fetch.cached_fetch(URL, producer)))
    assert second["status"] == "already_available"


@dataclass
class ToolConfig:
    enabled: bool = True
    externalize_min_chars: int = 12000
    fallback_max_chars: int = 4000
    preview_head_chars: int = 1000
    preview_tail_chars: int = 300
    fallback_head_chars: int = 1000
    fallback_tail_chars: int = 300
    storage_subdir: str = ".tool-results"
    exempt_tools: list = field(default_factory=lambda: ["read_file"])
    tool_overrides: dict = field(default_factory=dict)


@dataclass
class Message:
    content: object
    tool_call_id: str = "call-1"
    name: str = "web_fetch"
    response_metadata: dict = field(default_factory=dict)
    additional_kwargs: dict = field(default_factory=dict)
    def model_copy(self, update):
        return replace(self, **update)


@pytest.fixture
def native(monkeypatch):
    # The existing interpreter lacks LangChain. Stub framework shells only;
    # load and exercise the complete tracked middleware's production logic.
    class Middleware:
        def __class_getitem__(cls, item):
            return cls
    def no_provider():
        raise AssertionError("host/offline path must not acquire a provider")
    modules = {
        "langchain.agents": {"AgentState": dict},
        "langchain.agents.middleware": {"AgentMiddleware": Middleware},
        "langchain.agents.middleware.types": {"ModelCallResult": object, "ModelRequest": object, "ModelResponse": object},
        "langchain_core.messages": {"ToolMessage": Message},
        "langgraph.prebuilt.tool_node": {"ToolCallRequest": object},
        "langgraph.types": {"Command": type("Command", (), {})},
        "deerflow.config.tool_output_config": {"ToolOutputConfig": ToolConfig},
        "deerflow.sandbox.sandbox_provider": {"get_sandbox_provider": no_provider},
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("archive_native_budget", BRIDGE / "patches/middlewares/tool_output_budget_middleware.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("native_storage", [False, True])
def test_native_preview_uses_durable_id_even_if_externalize_fails(active, native, tmp_path, native_storage):
    archive, _, workspace, _ = active
    message = Message(BODY, response_metadata={"provider": "real"}, additional_kwargs={"source": "unchanged"})
    result = native._patch_tool_message(message, ToolConfig(), str(tmp_path / "outputs") if native_storage else None)
    artifact_id = hashlib.sha256(BODY.encode()).hexdigest()
    assert "read_evidence" in result.content and artifact_id in result.content
    assert len(result.content) <= 4000
    assert result.response_metadata == message.response_metadata
    assert result.additional_kwargs == message.additional_kwargs
    assert message.content == BODY
    assert workspace.read_artifact(workspace.lookup_ref(artifact_id)) == BODY
    assert "TAIL_NEEDLE" in archive.read_evidence(artifact_id, query="TAIL_NEEDLE")["text"]
    assert archive.source_rows() == []


def test_native_archive_failure_never_returns_truncated_view(active, native, monkeypatch):
    _, _, workspace, compaction = active
    def fail(*args, **kwargs):
        raise OSError("archive unavailable")
    monkeypatch.setattr(workspace, "put_artifact", fail)
    with pytest.raises(compaction.ResearchCompactionError):
        native._patch_tool_message(Message(BODY), ToolConfig(), None)
    assert compaction.get_compaction_stop() is not None


def test_native_flag_off_retains_seed_behavior(active, native, monkeypatch):
    archive, _, workspace, _ = active
    monkeypatch.setenv("RESEARCH_ENGINE", "legacy")
    result = native._patch_tool_message(Message(BODY), ToolConfig(), None)
    assert "Persistent storage unavailable" in result.content
    assert "read_evidence" not in result.content
    assert workspace.snapshot()["artifacts"] == []
    assert archive.current_workspace() is None


def test_restart_in_fresh_process_uses_verified_workspace_body(active):
    archive, fetch, workspace, _ = active
    async def producer(_):
        return BODY
    asyncio.run(fetch.cached_fetch(URL, producer))
    script = '''
import asyncio, hashlib, sys
sys.path.insert(0, sys.argv[1])
import research_archive as archive
import cached_fetch
from research_workspace import ResearchWorkspace
workspace = ResearchWorkspace(sys.argv[2], {"run": "fixture", "engine": "agentic"})
archive.activate_workspace(workspace)
async def forbidden(_):
    raise AssertionError("Restart must not call any fetch delegate")
body = asyncio.run(cached_fetch.cached_fetch(sys.argv[3], forbidden))
assert "TAIL_NEEDLE" in body
print(hashlib.sha256(body.encode()).hexdigest())
'''
    result = subprocess.run([sys.executable, "-c", script, str(BRIDGE), str(workspace.root), URL],
                            capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == hashlib.sha256(BODY.encode()).hexdigest()


def test_old_disk_cache_is_not_relabelled_fresh_retrieval(active):
    archive, fetch, _, _ = active
    fetch._write_cache(fetch._cache_path(fetch._cache_root(), URL), URL, BODY)
    async def forbidden(_):
        raise AssertionError("Disk cache should supply full body")
    assert asyncio.run(fetch.cached_fetch(URL, forbidden)) == BODY
    row = archive.source_rows()[0]
    assert row["source_origin"] == "cache"
    assert row["provenance"]["origin"] == "cache"
    assert archive.attach_source_content([row])[0]["content"] == BODY


def test_corrupt_control_disk_cache_cannot_become_fetched_source(active):
    archive, fetch, _, _ = active
    fetch._write_cache(fetch._cache_path(fetch._cache_root(), URL), URL,
                       json.dumps({"status": "already_available", "message": "x" * 800}))
    calls = []
    async def producer(_):
        calls.append(1)
        return BODY
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == BODY
    assert calls == [1]
    assert archive.attach_source_content(archive.source_rows())[0]["content"] == BODY


def test_source_receipt_matches_real_fetch_ledger_identity(active):
    archive, fetch, _, _ = active
    async def producer(_):
        fetch._FETCH_PROVIDER.set("jina")
        return BODY
    asyncio.run(fetch.cached_fetch(URL, producer))
    assert archive.source_rows()[0]["receipt_id"] == fetch._research_budget.list_fetched_sources()[0]["receipt_id"]


def test_attach_can_recall_both_archived_versions_without_changing_task_status(active):
    archive, _, workspace, _ = active
    task_id = "source:" + hashlib.sha256(URL.encode()).hexdigest()
    workspace.save_task(task_id, {"original": "input"}, {"original": "done"})
    before = workspace.snapshot()["tasks"]
    first = archive.archive_fetched_source(URL, BODY, provider="jina")
    second_body = BODY.replace("98765", "12345")
    second = archive.archive_fetched_source(URL, second_body, provider="jina")
    attached = archive.attach_source_content([first, second])
    assert [row["content"] for row in attached] == [BODY, second_body]
    assert workspace.snapshot()["tasks"] == before


@pytest.mark.parametrize("method", ["jina", "firecrawl", "exa", "direct"])
def test_fetch_provider_paths_retain_tail_before_native_offloading(active, monkeypatch, method):
    _, fetch, _, _ = active
    if method == "jina":
        class Jina:
            async def crawl(self, *args, **kwargs):
                return "html fixture"
        module = types.ModuleType("deerflow.community.jina_ai.tools")
        module.JinaClient = Jina
        module.get_app_config = lambda: types.SimpleNamespace(get_tool_config=lambda _: None)
        module.readability_extractor = types.SimpleNamespace(extract_article=lambda _: types.SimpleNamespace(to_markdown=lambda: BODY))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        result = asyncio.run(fetch._jina_delegate_fetch(URL))
    elif method == "exa":
        class Exa:
            def __init__(self, **kwargs):
                pass
            def get_contents(self, urls, text):
                assert text is True  # No request-side max_characters truncation.
                return types.SimpleNamespace(results=[types.SimpleNamespace(title="Source", text=BODY)])
        module = types.ModuleType("exa_py")
        module.Exa = Exa
        monkeypatch.setitem(sys.modules, "exa_py", module)
        monkeypatch.setenv("EXA_API_KEY", "offline-fixture")
        result = asyncio.run(fetch._exa_fetch(URL)).removeprefix("# Source\n\n")
    else:
        class Client:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, *args, **kwargs):
                return types.SimpleNamespace(status_code=200, json=lambda: {"data": {"markdown": BODY}})
            async def get(self, *args, **kwargs):
                return types.SimpleNamespace(status_code=200, content=BODY.encode(), text=BODY, headers={"content-type": "text/plain"})
        module = types.ModuleType("httpx")
        module.AsyncClient = Client
        monkeypatch.setitem(sys.modules, "httpx", module)
        if method == "firecrawl":
            monkeypatch.setenv("FIRECRAWL_API_KEY", "offline-fixture")
            monkeypatch.setattr(fetch, "_firecrawl_fetch_calls", 0)
            result = asyncio.run(fetch._firecrawl_fetch(URL))
        else:
            async def public(_):
                return True
            monkeypatch.setattr(fetch, "_host_is_public", public)
            result = asyncio.run(fetch._direct_http_fetch(URL))
    assert result == BODY


def test_native_read_evidence_is_already_bounded_and_not_rearchived(active, native):
    _, _, workspace, _ = active
    message = Message(BODY, name="read_evidence")
    assert native._patch_tool_message(message, ToolConfig(), None) is message
    assert not native._tool_message_over_budget(message, ToolConfig())
    assert workspace.snapshot()["artifacts"] == []


def test_native_storage_exception_uses_archive_and_preserves_structured_metadata(active, native, monkeypatch):
    archive, _, workspace, _ = active
    def unavailable(*args, **kwargs):
        raise OSError("native disk failed")
    monkeypatch.setattr(native, "_externalize", unavailable)
    content = [{"type": "text", "text": BODY, "source_metadata": {"uri": URL}}]
    result = native._patch_tool_message(Message(content), ToolConfig(), "/irrelevant/offline/path")
    ref = next(e["payload"]["ref"] for e in workspace.snapshot()["events"] if e["kind"] == "native_tool_result")
    assert ref["id"] in result.content
    assert json.loads(workspace.read_artifact(ref)) == content
    assert archive.source_rows() == []


def test_cancelled_fetch_follower_does_not_leak_singleflight_lock(active):
    _, fetch, _, _ = active
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def producer(_):
            calls.append(1)
            started.set()
            await release.wait()
            return BODY
        owner = asyncio.create_task(fetch.cached_fetch(URL, producer))
        await started.wait()
        follower = asyncio.create_task(fetch.cached_fetch(URL, producer))
        await asyncio.sleep(0.01)
        follower.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower
        release.set()
        assert await owner == BODY
        assert await fetch.cached_fetch(URL, producer) == BODY
        assert calls == [1]
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("source_origin", "fetched"), ("provider", "jina"),
                                        ("receipt_id", "native-receipt")])
def test_attach_preserves_native_metadata_when_exact_cached_body_matches(active, field, value):
    archive, _, _, _ = active
    source = archive.archive_fetched_source(URL, BODY, provider="cache", cache_hit=True)
    native = dict(source, **{field: value})
    before = copy.deepcopy(native)
    assert archive.attach_source_content([native])[0] == {**before, "content": BODY}
    assert native == before
    assert archive.source_rows() == [source]


def test_canonical_native_fetched_export_gets_cached_tail_without_provenance_rewrite(active):
    archive, _, _, _ = active
    archived = archive.archive_fetched_source(URL, BODY, provider="cache", cache_hit=True)
    before_events = archive.source_rows()
    native = {
        "url": URL, "content_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
        "source_origin": "fetched", "status": "ok", "provider": "jina",
        "receipt_id": "parent-producer-receipt", "cache_hit": True, "cache_hits": 3,
        "provenance": {"producer": "native-collector", "lane": "actor"},
        "reachable": True, "excerpt": BODY[:200], "title": "Canonical source",
    }
    original = copy.deepcopy(native)
    exported = json.loads(json.dumps(archive.attach_source_content([native]), ensure_ascii=False))[0]
    assert exported == {**original, "content": BODY}
    assert exported["content"].endswith("TAIL_NEEDLE: final revised figure is 98765.\r\n")
    assert "ref" not in exported and "content_ref" not in exported
    assert native == original
    assert archive.source_rows() == before_events == [archived]
    assert archived["source_origin"] == "cache"
    assert archived["provider"] == "cache"
    assert all("content" not in row for row in archive.attach_source_content([
        dict(native, url=URL + "/different"), dict(native, content_sha256="f" * 64),
        dict(native, source_origin="cited"), dict(native, source_origin="search_snippet"),
    ]))


def test_attachment_reads_only_requested_source_events_once_per_url(active, monkeypatch):
    archive, _, workspace, _ = active
    first = archive.archive_fetched_source(URL, BODY, provider="cache", cache_hit=True)
    second_body = BODY.replace("98765", "12345")
    second = archive.archive_fetched_source(URL, second_body, provider="jina")
    irrelevant = workspace.put_artifact("unrelated historical task" * 500, "task_result")
    workspace.save_task("old-task", {"question": "not requested"}, {"ref": irrelevant})
    archive.archive_fetched_source(URL + "/unrelated", BODY + "other", provider="jina")
    calls = []
    original_events = workspace.events
    original_read = workspace.read_artifact
    def no_snapshot():
        raise AssertionError("Attachment must not scan unrelated task/event history")
    def only_requested(task_id):
        calls.append(task_id)
        assert task_id == "source:" + hashlib.sha256(URL.encode()).hexdigest()
        return original_events(task_id)
    def only_source_body(ref):
        assert ref["id"] in {first["ref"]["id"], second["ref"]["id"]}
        return original_read(ref)
    monkeypatch.setattr(workspace, "snapshot", no_snapshot)
    monkeypatch.setattr(workspace, "events", only_requested)
    monkeypatch.setattr(workspace, "read_artifact", only_source_body)
    row = {"url": URL, "source_origin": "fetched", "provider": "native",
           "receipt_id": "native", "content_sha256": first["content_sha256"]}
    rows = [row, dict(row, content_sha256=second["content_sha256"]), dict(row)]
    attached = archive.attach_source_content(rows)
    assert [item["content"] for item in attached] == [BODY, second_body, BODY]
    assert len(calls) == 1
    assert all({key: value for key, value in item.items() if key != "content"} == original
               for item, original in zip(attached, rows, strict=True))


@pytest.mark.parametrize("rows", [[], [{"source_origin": "cited", "url": URL, "content_sha256": "0" * 64}],
    [{"source_origin": "search_snippet", "url": URL, "content_sha256": "0" * 64}],
    [{"source_origin": "fetched", "url": URL}], [{"source_origin": "fetched", "url": URL, "content_sha256": "not-a-sha"}]])
def test_attachment_skips_store_io_when_no_eligible_body_rows(active, monkeypatch, rows):
    archive, _, workspace, _ = active
    def forbidden(*args, **kwargs):
        raise AssertionError("No eligible body row needs archive I/O")
    monkeypatch.setattr(workspace, "snapshot", forbidden)
    monkeypatch.setattr(workspace, "events", forbidden)
    assert archive.attach_source_content(rows) == rows


def test_source_rows_reads_only_native_source_event_kind(active, monkeypatch):
    archive, _, workspace, _ = active
    original = archive.archive_fetched_source(URL, BODY, provider="cache", cache_hit=True)
    latest = archive.archive_fetched_source(URL, BODY + "revision", provider="jina")
    archive.archive_tool_result(json.dumps({"source_origin": "fetched", "url": URL + "/invented"}), "task")
    workspace.save_task("unrelated-task", {"question": "other"}, {"report": "history " * 3000})
    def no_snapshot():
        raise AssertionError("Source replay must not scan old task results")
    monkeypatch.setattr(workspace, "snapshot", no_snapshot)
    assert archive.source_rows() == [latest]
    assert original["source_origin"] == "cache"


def test_actual_native_metadata_export_skips_body_reads_but_final_export_attaches(active, monkeypatch):
    archive, _, workspace, _ = active
    source = archive.archive_fetched_source(URL, BODY, provider="cache", cache_hit=True)
    spec = importlib.util.spec_from_file_location("archive_native_export_fixture", BRIDGE / "deerflow_research.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    monkeypatch.setattr(native, "_merge_shared_fetched_sources", lambda: None)
    monkeypatch.setattr(native, "_FETCHED_SOURCES", [{
        "url": URL, "ok": True, "title": "Native source", "excerpt": BODY[:1200],
        "content_sha256": source["content_sha256"], "content_chars": len(BODY),
        "provider": "jina", "receipt_id": "native-receipt", "cache_hits": 4,
    }])
    calls = []
    original_events = workspace.events
    def events(task_id):
        calls.append(task_id)
        return original_events(task_id)
    def no_snapshot():
        raise AssertionError("Native export cannot scan historical task results")
    monkeypatch.setattr(workspace, "events", events)
    monkeypatch.setattr(workspace, "snapshot", no_snapshot)
    for _ in range(3):
        metadata = native.export_fetched_sources_for_manifest(include_content=False)
        assert metadata[0]["source_origin"] == "fetched"
        assert "content" not in metadata[0]
        assert calls == []
    final = native.export_fetched_sources_for_manifest()
    assert final == [{**metadata[0], "content": BODY}]
    assert len(calls) == 1


def test_corrupt_managed_evidence_latches_terminal_stop(active):
    archive, _, workspace, compaction = active
    ref = archive.archive_tool_result(BODY, "web_fetch")
    (workspace.root / ref["path"]).write_text("corrupt")
    with pytest.raises(compaction.ResearchCompactionError):
        archive.read_evidence(ref["id"])
    assert compaction.get_compaction_stop() is not None


def test_unknown_id_is_validation_error_without_stopping_other_work(active):
    archive, _, _, compaction = active
    with pytest.raises((ValueError, KeyError, RuntimeError)):
        archive.read_evidence("0" * 64)
    assert compaction.get_compaction_stop() is None


@pytest.mark.parametrize("content", [{1: "key would change"}, ("tuple",), {"value": float("nan")}])
def test_tool_archive_rejects_lossy_non_json_metadata(active, content):
    archive, _, workspace, compaction = active
    with pytest.raises(compaction.ResearchCompactionError):
        archive.archive_tool_result(content, "task")
    assert workspace.snapshot()["artifacts"] == []


@pytest.mark.parametrize("invalid_range", ["chars:20:10", "chars:-1:10", "chars:abc:10", "chars:1:", "chars=bad", "chars:1.5:10"])
def test_native_tool_has_only_id_query_and_supports_explicit_range_recall(active, monkeypatch, invalid_range):
    archive, _, workspace, _ = active
    import inspect
    # Stub decorator only, exercising the actual exported implementation.
    module = types.ModuleType("langchain_core.tools")
    module.tool = lambda *args, **kwargs: lambda function: function
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = importlib.util.spec_from_file_location("archive_tool_schema_fixture", BRIDGE / "research_archive.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    loaded.activate_workspace(workspace)
    assert list(inspect.signature(loaded.read_evidence_tool).parameters) == ["artifact_id", "query"]
    ref = archive.archive_tool_result(BODY, "task")
    start = BODY.index("TAIL_NEEDLE")
    text = loaded.read_evidence_tool(ref["id"], f"chars:{start}:{len(BODY)}")
    assert "TAIL_NEEDLE" in text
    with pytest.raises(ValueError):
        loaded.read_evidence_tool(ref["id"], invalid_range)


def test_nested_json_fields_remain_usable_source_data(active):
    archive, fetch, _, _ = active
    body = json.dumps({"source_origin": {"system": "data"}, "evidence_kind": ["table"], "rows": list(range(300))})
    async def producer(_):
        return body
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == body
    assert archive.attach_source_content(archive.source_rows())[0]["content"] == body


def test_workspace_cannot_switch_during_an_active_fetch(active, tmp_path):
    archive, fetch, workspace, _ = active
    foreign = type(workspace)(tmp_path / "second-run", {"run": "foreign"})
    async def producer(_):
        with pytest.raises(RuntimeError):
            archive.activate_workspace(foreign)
        assert archive.current_workspace() is workspace
        return BODY
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == BODY
    assert foreign.snapshot()["events"] == []


def test_without_active_workspace_agentic_flag_retains_legacy(active):
    archive, fetch, _, _ = active
    archive.activate_workspace(None)
    async def producer(_):
        return BODY
    assert asyncio.run(fetch.cached_fetch(URL, producer)) == BODY
    assert json.loads(asyncio.run(fetch.cached_fetch(URL, producer)))["status"] == "already_available"


def test_native_async_hook_archives_before_returning_view(active, native):
    archive, _, workspace, _ = active
    request = types.SimpleNamespace(runtime=types.SimpleNamespace(state={}))
    message = Message(BODY)
    async def handler(_):
        return message
    result = asyncio.run(native.ToolOutputBudgetMiddleware(ToolConfig()).awrap_tool_call(request, handler))
    ref = workspace.lookup_ref(hashlib.sha256(BODY.encode()).hexdigest())
    assert ref["id"] in result.content
    assert "TAIL_NEEDLE" in archive.read_evidence(ref["id"], query="TAIL_NEEDLE")["text"]
