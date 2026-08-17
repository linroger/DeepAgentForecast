"""Offline unit tests for research-stage prompt caching (token lever 1).

Same harness as test_wave9_research_speed.py: ``claude_provider.py`` imports
anthropic / langchain_anthropic / langchain_core at module level (absent from
backend/.venv), so minimal stub modules are registered before a path-based load.
Zero network, zero real SDK clients — the tests build Anthropic request payloads
and assert cache_control placement/structure, pin the DEERFLOW_CLAUDE_PROMPT_CACHE
kill-switch contract, and guard byte-identity between the tracked patch sources
and their deployed runtime copies.
"""

import importlib.util
import os
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "deerflow_bridge")
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

import deerflow_research as dr  # noqa: E402


def _load_claude_provider():
    """Stub anthropic / langchain_anthropic / langchain_core, then load by path.

    Same stub shapes as test_wave9_research_speed.py, but every stub THIS loader
    registered is popped from sys.modules after the load: deerflow_research's
    ``_stage1_model_messages`` relies on ``from langchain_core.messages import
    HumanMessage`` raising ImportError in the backend venv, so a stub left behind
    would poison later-collected suites (test_stage1_model_boundaries) with a
    no-arg HumanMessage. The loaded provider module keeps direct references to
    the stub classes it imported, so tests reach them via the module object
    (e.g. ``cp.ChatAnthropic``), never via sys.modules.
    """
    if "cacheprobe_claude_provider" in sys.modules:
        return sys.modules["cacheprobe_claude_provider"]
    created: list[str] = []
    if "anthropic" not in sys.modules:
        anthropic_stub = types.ModuleType("anthropic")
        anthropic_stub.RateLimitError = type("RateLimitError", (Exception,), {})
        anthropic_stub.InternalServerError = type("InternalServerError", (Exception,), {})
        sys.modules["anthropic"] = anthropic_stub
        created.append("anthropic")
    if "langchain_anthropic" not in sys.modules:
        lc_anthropic = types.ModuleType("langchain_anthropic")
        lc_anthropic.ChatAnthropic = type("ChatAnthropic", (), {})
        sys.modules["langchain_anthropic"] = lc_anthropic
        created.append("langchain_anthropic")
    if "langchain_core.messages" not in sys.modules:
        lc_core = sys.modules.get("langchain_core")
        if lc_core is None:
            lc_core = types.ModuleType("langchain_core")
            sys.modules["langchain_core"] = lc_core
            created.append("langchain_core")
        lc_msgs = types.ModuleType("langchain_core.messages")
        lc_msgs.BaseMessage = type("BaseMessage", (), {})
        lc_msgs.HumanMessage = type("HumanMessage", (), {})
        lc_core.messages = lc_msgs
        sys.modules["langchain_core.messages"] = lc_msgs
        created.append("langchain_core.messages")
    try:
        path = os.path.join(_BRIDGE_DIR, "patches", "models", "claude_provider.py")
        spec = importlib.util.spec_from_file_location("cacheprobe_claude_provider", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["cacheprobe_claude_provider"] = mod
        spec.loader.exec_module(mod)
    finally:
        for name in created:
            sys.modules.pop(name, None)
    return mod


def _instance(mod, *, enable=True, cache_size=3, oauth=False):
    """Build a ClaudeChatModel without running pydantic/langchain init machinery."""
    obj = object.__new__(mod.ClaudeChatModel)
    obj.enable_prompt_caching = enable
    obj.prompt_cache_size = cache_size
    obj.auto_thinking_budget = False
    obj._is_oauth = oauth
    return obj


def _payload(*, system="You are the deep-research lead agent. " * 40, n_msgs=6, with_tools=True):
    """A representative agentic-thread payload (post _format_messages shape)."""
    messages = []
    for i in range(n_msgs):
        if i % 2 == 0:
            messages.append(
                {"role": "user", "content": [{"type": "text", "text": f"user turn {i}"}]}
            )
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": f"reasoning {i}", "signature": "sig"},
                        {"type": "text", "text": f"assistant turn {i}"},
                        {
                            "type": "tool_use",
                            "id": f"tu_{i}",
                            "name": "web_search",
                            "input": {"query": f"q{i}"},
                        },
                    ],
                }
            )
    payload = {"model": "claude-opus-4-8", "max_tokens": 64000, "messages": messages}
    if system is not None:
        payload["system"] = system
    if with_tools:
        payload["tools"] = [
            {"name": "web_search", "description": "search", "input_schema": {"type": "object"}},
            {"name": "web_fetch", "description": "fetch", "input_schema": {"type": "object"}},
        ]
    return payload


def _marked_blocks(payload):
    """Collect every block carrying cache_control, labeled by section."""
    found = []
    system = payload.get("system")
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and "cache_control" in block:
                found.append(("system", block))
    for i, msg in enumerate(payload.get("messages", [])):
        if isinstance(msg, dict) and "cache_control" in msg:
            found.append((f"message-dict-{i}", msg))
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    found.append((f"msg{i}", block))
    for tool in payload.get("tools", []) or []:
        if isinstance(tool, dict) and "cache_control" in tool:
            found.append(("tool", tool))
    return found


# ================================================================ 1) breakpoint placement


def test_default_marks_system_anchor_and_newest_message_blocks(monkeypatch):
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp)
    payload = _payload()

    obj._apply_prompt_caching(payload)

    marks = _marked_blocks(payload)
    assert len(marks) == 4  # exactly the API breakpoint budget
    sections = [label for label, _ in marks]
    # str system was normalized into a block list and its LAST text block anchors
    # the stable prefix (render order tools -> system -> messages means this one
    # breakpoint caches tools + system together).
    assert isinstance(payload["system"], list)
    assert sections.count("system") == 1
    assert "cache_control" in payload["system"][-1]
    # No separate tool marker when the system anchor exists — it would waste a slot.
    assert "tool" not in sections
    # Incremental-conversation markers: last cacheable block of each of the
    # newest prompt_cache_size (3) messages.
    assert {"msg3", "msg4", "msg5"} == {s for s in sections if s.startswith("msg")}
    msgs = payload["messages"]
    assert "cache_control" in msgs[5].get("content")[-1]  # assistant tool_use block
    assert "cache_control" in msgs[4].get("content")[-1]  # user text block
    assert "cache_control" in msgs[3].get("content")[-1]
    for _, block in marks:
        assert block["cache_control"] == {"type": "ephemeral"}


def test_thinking_blocks_are_never_marked(monkeypatch):
    """thinking/redacted_thinking cannot carry cache_control (API 400)."""
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp)
    payload = _payload(n_msgs=1)
    payload["messages"].append(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "before"},
                {"type": "thinking", "thinking": "tail reasoning", "signature": "sig"},
            ],
        }
    )

    obj._apply_prompt_caching(payload)

    for _, block in _marked_blocks(payload):
        assert block.get("type") != "thinking"
    # The marker for the trailing message lands on its last CACHEABLE block.
    assert "cache_control" in payload["messages"][-1]["content"][0]
    assert "cache_control" not in payload["messages"][-1]["content"][1]


def test_no_system_falls_back_to_tools_anchor(monkeypatch):
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp)
    payload = _payload(system=None)

    obj._apply_prompt_caching(payload)

    marks = _marked_blocks(payload)
    sections = [label for label, _ in marks]
    assert sections.count("tool") == 1
    assert "cache_control" in payload["tools"][-1]
    assert len(marks) == 4


def test_marker_budget_never_exceeds_api_limit(monkeypatch):
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp, cache_size=10)
    payload = _payload(n_msgs=40)

    obj._apply_prompt_caching(payload)

    assert len(_marked_blocks(payload)) <= cp.MAX_CACHE_BREAKPOINTS == 4


def test_apply_strips_stray_markers_first_and_is_idempotent(monkeypatch):
    """Upstream/leftover markers must never push the request over 4 breakpoints."""
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp)
    payload = _payload()
    # Stray markers deep in the prefix (e.g. from an earlier pass or upstream code).
    payload["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral"}
    payload["tools"][0]["cache_control"] = {"type": "ephemeral"}

    obj._apply_prompt_caching(payload)
    first = [(label, id(block)) for label, block in _marked_blocks(payload)]
    assert len(first) == 4
    assert "cache_control" not in payload["messages"][0]["content"][0]
    assert "cache_control" not in payload["tools"][0]

    obj._apply_prompt_caching(payload)
    second = [(label, id(block)) for label, block in _marked_blocks(payload)]
    assert first == second  # idempotent under repeated application


def test_string_message_content_is_normalized_and_marked(monkeypatch):
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    obj = _instance(cp)
    payload = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "plain string turn"}],
    }

    obj._apply_prompt_caching(payload)

    content = payload["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["cache_control"] == {"type": "ephemeral"}


# ================================================================ 2) kill-switch contract


def test_kill_switch_env_contract(monkeypatch):
    cp = _load_claude_provider()
    obj = _instance(cp, enable=True)

    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    assert obj._prompt_cache_enabled() is True  # default ON

    for off in ("0", "false", "no", "off", "FALSE", " Off "):
        monkeypatch.setenv("DEERFLOW_CLAUDE_PROMPT_CACHE", off)
        assert obj._prompt_cache_enabled() is False, off

    disabled = _instance(cp, enable=False)
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    assert disabled._prompt_cache_enabled() is False  # config still governs when unset
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("DEERFLOW_CLAUDE_PROMPT_CACHE", on)
        assert disabled._prompt_cache_enabled() is True, on

    monkeypatch.setenv("DEERFLOW_CLAUDE_PROMPT_CACHE", "maybe")  # garbage → config value
    assert obj._prompt_cache_enabled() is True
    assert disabled._prompt_cache_enabled() is False


def test_strip_cache_control_clears_every_section(monkeypatch):
    cp = _load_claude_provider()
    payload = _payload()
    payload["system"] = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    payload["messages"][-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}

    cp.ClaudeChatModel._strip_cache_control(payload)

    assert _marked_blocks(payload) == []


# ================================================================ 3) end-to-end payload build (stubbed SDK)


def _wire_fake_super_payload(monkeypatch, cp, canned_factory):
    # cp.ChatAnthropic is the stub base captured at module load — reaching it via
    # the module keeps this independent of sys.modules (the loader cleans those up).
    monkeypatch.setattr(
        cp.ChatAnthropic,
        "_get_request_payload",
        lambda self, input_, stop=None, **kwargs: canned_factory(),
        raising=False,
    )


def test_oauth_request_payload_carries_billing_and_cache_breakpoints(monkeypatch):
    """The subscription path now sends cache_control (this is lever 1)."""
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    _wire_fake_super_payload(monkeypatch, cp, _payload)
    obj = _instance(cp, oauth=True)

    payload = obj._get_request_payload("ignored-input")

    # OAuth billing block is first in system and itself byte-stable per call.
    assert payload["system"][0]["text"] == cp.OAUTH_BILLING_HEADER
    assert "user_id" in payload["metadata"]
    marks = _marked_blocks(payload)
    assert len(marks) == 4
    # The billing block never carries the anchor — the LAST system text block does,
    # so the cached prefix covers tools + billing + system in one breakpoint.
    assert "cache_control" not in payload["system"][0]
    assert "cache_control" in payload["system"][-1]


def test_kill_switch_restores_marker_free_oauth_payload(monkeypatch):
    """DEERFLOW_CLAUDE_PROMPT_CACHE=0 pins the historical no-marker OAuth payload."""
    monkeypatch.setenv("DEERFLOW_CLAUDE_PROMPT_CACHE", "0")
    cp = _load_claude_provider()

    def canned_with_stray():
        payload = _payload()
        payload["messages"][2]["content"][0]["cache_control"] = {"type": "ephemeral"}
        return payload

    _wire_fake_super_payload(monkeypatch, cp, canned_with_stray)
    obj = _instance(cp, oauth=True)

    payload = obj._get_request_payload("ignored-input")

    assert _marked_blocks(payload) == []  # not one marker leaves the client
    assert payload["system"][0]["text"] == cp.OAUTH_BILLING_HEADER  # billing unaffected


def test_api_key_request_payload_also_caches(monkeypatch):
    monkeypatch.delenv("DEERFLOW_CLAUDE_PROMPT_CACHE", raising=False)
    cp = _load_claude_provider()
    _wire_fake_super_payload(monkeypatch, cp, _payload)
    obj = _instance(cp, oauth=False)

    payload = obj._get_request_payload("ignored-input")

    assert len(_marked_blocks(payload)) == 4
    assert "metadata" not in payload  # OAuth billing untouched on the API-key path


# ================================================================ 4) usage accounting stays honest


def test_model_response_usage_reads_cache_inclusive_totals():
    """langchain-anthropic folds cache_read/cache_creation into usage_metadata's
    input_tokens (its _create_usage_metadata sums base + cache_read + cache_creation),
    so the bridge's usage extraction keeps honest per-call totals with caching ON and
    the cached split stays visible as additive detail keys."""
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100_000,  # 2_000 uncached + 95_000 cache_read + 3_000 cache_creation
            "output_tokens": 2_000,
            "total_tokens": 102_000,
            "input_token_details": {"cache_read": 95_000, "cache_creation": 3_000},
        },
        response_metadata={},
    )
    assert dr._model_response_usage(response) == (100_000, 2_000, 102_000)


# ================================================================ 5) tracked patch sources deploy byte-identically


def _git_dirty_patch_paths():
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", "deerflow_bridge/patches"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    dirty = set()
    for line in proc.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.add(os.path.normpath(os.path.join(_REPO_ROOT, path)))
    return dirty


class TestTrackedProviderPatchesDeployedByteIdentical:
    """patches/models is deployed only by setup.sh (the runtime drift guard syncs
    middlewares but not models), so a bridge-side provider edit can silently go
    stale in deer-flow/. Same conventions as the skill byte-identity suite:
    skip when the deployed tree is absent, skip in-flight (git-dirty) sources."""

    _SYNC_MAP = (
        (
            os.path.join("deerflow_bridge", "patches", "models"),
            os.path.join("deer-flow", "backend", "packages", "harness", "deerflow", "models"),
        ),
        (
            os.path.join("deerflow_bridge", "patches", "middlewares"),
            os.path.join(
                "deer-flow", "backend", "packages", "harness", "deerflow", "agents", "middlewares"
            ),
        ),
    )

    def test_deployed_provider_and_middleware_patches_match_sources(self):
        deployed_root = os.path.join(_REPO_ROOT, "deer-flow")
        if not os.path.isdir(deployed_root):
            pytest.skip("deer-flow absent (fresh checkout without the deployed tree)")
        dirty = _git_dirty_patch_paths()
        if dirty is None:
            pytest.skip("git unavailable — cannot determine the in-flight edit set")
        compared = 0
        for src_rel, dst_rel in self._SYNC_MAP:
            src_dir = os.path.join(_REPO_ROOT, src_rel)
            dst_dir = os.path.join(_REPO_ROOT, dst_rel)
            if not os.path.isdir(dst_dir):
                continue
            for name in sorted(os.listdir(src_dir)):
                if not name.endswith(".py"):
                    continue
                src = os.path.normpath(os.path.join(src_dir, name))
                if src in dirty:
                    continue  # in-flight edit: deployed copy may legitimately lag
                dst = os.path.join(dst_dir, name)
                assert os.path.isfile(dst), (
                    f"{src_rel}/{name} is tracked but not deployed at {dst_rel}/{name} — "
                    f"re-run ./setup.sh (models) or the orchestrator drift guard (middlewares)"
                )
                with open(src, "rb") as fh:
                    src_bytes = fh.read()
                with open(dst, "rb") as fh:
                    dst_bytes = fh.read()
                assert src_bytes == dst_bytes, (
                    f"{dst_rel}/{name} drifted from the tracked source "
                    f"(src={len(src_bytes)}B deployed={len(dst_bytes)}B) — re-sync before running research"
                )
                compared += 1
        if compared == 0:
            pytest.skip("no clean tracked patch file had a deployed counterpart to compare")
